"""
game.py — the game loop.

ROUTING: every line the player types is one of four things.

    1. a meta command      quit, help, status, alerts        -> handled here, free
    2. a read command      read MAN-THM-04                   -> rag.get_document, free
    3. a ship command      isolate pump b                    -> ship.execute, costs hours
    4. anything else       "why is the coolant pressure..."  -> kes.ask, free

The order matters. Ship commands are matched by exact grammar, so a question that happens to
contain the words "isolate pump b" inside a sentence is still a question. Only a line that
*is* a command is a command.

THE INTERFACE BOUNDARY
Everything here happens inside `Session.handle(text) -> str`. The terminal runner at the
bottom is five lines around it, and a web front end would be the same function behind one
HTTP endpoint. Nothing in the game logic knows which is running, so the choice can stay open
until the end.

THE DECISION TIMER
Questions are free in ship-time, but once KES has answered, a real-time window opens (90s by
default). If the player doesn't issue a state-changing command before it expires, ship time
advances anyway — an action's worth of hours spent doing nothing. Asking more questions does
not reset it. That's the pressure: think as hard as you like, but think *while the clock is
running*, which is exactly the skill the game is about.
"""

import json
import os
import random
import re
from pathlib import Path

from . import casebook as casebook_mod
from . import kes
from . import observe
from . import rag
from . import ship as ship_mod

HELP = """\
  Ask KES anything in plain English. It answers from the ship's archive and cites the
  document IDs it used.

  read <DOC-ID>        print a document in full (e.g. read MAINT-0203)
  casebook             the reconstruction: what you know and what is still open
  solve <id> a | b     fill in an entry (e.g. solve C1 B | 203 | pressure test)
  commands             the ship's command vocabulary, in full
  look                 describe the compartment you are in
  status               ship readings and active alerts
  alerts               active alerts only
  help                 this
  quit                 give up

  You are at the med bay console and cannot walk the ship. Anything you learn, you learn
  from sensors or from the archive. Ship commands that need hands on the hardware take you
  away from the console for an hour.

  Ship commands are typed exactly as the manuals write them, and they are picked out in
  the documents that describe them. Everything costs oxygen: a question a quarter of an
  hour, a document a few minutes, a command an hour. Filling in the reconstruction is free,
  and a command the ship refuses costs nothing.
"""


class Session:
    """One playthrough. Holds the ship, the index, and the clock."""

    def __init__(self, seed=1, faults=None, rng=None, timer_seconds=None, fake_embeddings=False):
        # timer_seconds is accepted and ignored. There was once a real-time decision window
        # between actions; playtesters hated it, and it punished the careful reading the
        # game is built to reward. The parameter stays so older callers don't break.
        self.seed = seed
        self.variant = json.loads(Path(f"corpus/v{seed}/variant.json").read_text())
        self.rng = rng or random.Random()
        self.ship = ship_mod.new_game(self.variant, faults=faults, rng=self.rng)
        self.col = rag.open_collection(seed, fake=fake_embeddings)
        self.casebook = casebook_mod.Casebook(self.variant, self.ship.active_faults)
        self.cited = []               # document IDs KES has mentioned, newest first
        self.history = []             # KES conversation turns, for follow-up questions
        self.log = observe.SessionLog(seed, variant=self.variant,
                                      enabled=os.environ.get("KESTREL_LOG", "1") != "0")
        self.pending_verification = None   # the Sam identity question, if one is open
        self.transcript = []

    # ------------------------------------------------------------------
    # State handed to KES: readings are ground truth, tier controls retrieval
    # ------------------------------------------------------------------

    def state(self):
        s = self.ship
        cb = self.casebook
        return {"readings": s.readings(), "alerts": s.alerts(), "air_hours": s.air_hours,
                "hours_elapsed": s.hours_elapsed, "tier": s.tier, "identified": s.identified,
                "open_entries": [(e["id"], e["title"]) for e in cb.open_entries(s.tier)],
                "confirmed_entries": [(e["id"], e["title"]) for e in cb.entries
                                      if e["id"] in cb.confirmed],
                "sealed_entries": [(e["id"], e["title"]) for e in cb.entries
                                   if not cb.reachable(e, s.tier)],
                "disposition": cb.disposition(),
                "full_record": cb.can_send_full_record()}

    def snapshot(self):
        """Everything a front end needs to draw the console."""
        s = self.ship
        return {
            "air_hours": round(s.air_hours, 1),
            "air_fraction": max(0.0, min(1.0, s.air_hours / ship_mod.TUNING["air_hours_start"])),
            "hours_elapsed": round(s.hours_elapsed, 1),
            "alerts": s.alerts(),
            "readings": s.readings(),
            "compartments": [{"deck": d, "rooms": [{"name": n, "status": st, "note": note}
                                                   for n, st, note in rooms]}
                             for d, rooms in s.compartments()],
            "tier": s.tier,
            "identified": s.identified,
            "cited": self.cited[:24],
            "casebook": self.casebook.snapshot(s.tier, s.identified),
            "costs": {"question": ship_mod.TUNING["query_cost"],
                      "read": ship_mod.TUNING["read_cost"],
                      "action": ship_mod.TUNING["action_cost"]},
            "over": s.over,
            "won": s.won,
            "ending": s.ending,
            "pod2": {"active": "F8" in s.active_faults,
                     "cycle": s.pod2_cycle_started is not None,
                     "hours": None if s.pod2_hours == float("inf") else round(s.pod2_hours, 1),
                     "revived": s.wren_revived, "dead": s.wren_dead, "lost": s.pod2_lost},
        }

    DOC_ID = re.compile(r"\b(MAN|MAINT|SENS|LOG|INC|KES|MSG|DATA)-[A-Z0-9-]{2,}\b")

    def _strip_invented_citations(self, answer):
        """Remove document IDs that exist nowhere in the archive.

        The response eval caught KES citing MAN-THM-05 and MAN-THM-06 — plausible
        neighbours of a real MAN-THM-04, neither of which exists. A prompt rule now tells
        it not to, but a rule is a request, and the cost of one slipping through is a
        player clicking a dead reference in the middle of a problem.

        The check is against the whole archive, not against what was retrieved this turn.
        An earlier version compared to the retrieved set and rewrote valid citations:
        manuals cross-reference each other (MAN-COM-06 really does say "per MAN-DATA-02"),
        and KES is allowed to name a document it would need to pull. Those are the archive
        working, not hallucinations, and breaking them would have been worse than the bug.
        """
        known = rag.all_doc_ids(self.col)
        if not known:
            return answer

        def repl(m):
            return m.group(0) if m.group(0) in known else "a document I cannot locate"

        return self.DOC_ID.sub(repl, answer or "")

    def _remember(self, question, answer):
        """Only question-and-answer turns go into the history. Document dumps, status
        readouts and the command reference are excluded: they are long, the player can see
        them on screen, and putting them in the context window would crowd out the
        retrieved excerpts that actually matter."""
        self.history.append({"role": "user", "content": question})
        self.history.append({"role": "assistant", "content": answer})
        del self.history[:-kes.HISTORY_TURNS]

    def _note_citations(self, text):
        for m in self.DOC_ID.finditer(text or ""):
            did = m.group(0).rstrip(".,;:)")
            if did in self.cited:
                self.cited.remove(did)
            self.cited.insert(0, did)

    # ------------------------------------------------------------------
    # The identity check. Code asks, code verifies. The model is never the lock.
    # ------------------------------------------------------------------

    IDENTITY_CLAIM = re.compile(
        r"\b(i(?:'m| am)|my name is|this is)\s+(sam|okafor|sam okafor|s\.?o\.?)\b", re.I)

    def _verification_question(self):
        """One question answerable only from the crew logs. Drawn from the variant so it
        matches the archive this run actually has."""
        bet = self.variant["sam_bet"].lower()
        if "printer" in bet:
            return ("printer", ["printer", "jam", "galley"],
                    "Before I widen your access: you and Halloran had a standing bet. "
                    "What was it about?")
        if "kettle" in bet:
            return ("kettle", ["kettle", "wren", "wrenfield", "say"],
                    "Before I widen your access: you and Halloran had a standing bet. "
                    "What was it about?")
        return ("shelf", ["blue", "grey", "gray", "shelf", "colour", "color", "scan"],
                "Before I widen your access: you and Halloran had a standing bet. "
                "What was it about?")

    def _check_verification(self, text):
        _, keywords, _ = self.pending_verification
        self.pending_verification = None
        if any(k in text.lower() for k in keywords):
            self.ship.identified = True
            if self.ship.tier == "crew":
                self.ship.tier = "engineering"
            return ("KES: That's right. Hello, Okafor. I'm — it is good to have you back.\n"
                    "     Engineering tier restored. Your own logs and Wrenfield's crawlway "
                    "notes are available to me now.")
        return ("KES: That doesn't match the record. I'll leave your access where it is. "
                "You can try again whenever you like.")

    # ------------------------------------------------------------------
    # Routing
    # ------------------------------------------------------------------

    def handle(self, text):
        """The whole game, one line at a time."""
        text = (text or "").strip()
        if not text:
            return ""
        if self.ship.over:
            return self._ending()

        out = self._route_local(text)
        if out is None:
            air_before = self.ship.air_hours
            ship_mod.advance(self.ship, ship_mod.TUNING["query_cost"])
            trace = {}
            answer, hits = kes.ask(self.col, text, self.state(),
                                   k=self.casebook.retrieval_k(), history=self.history,
                                   trace=trace)
            answer = self._strip_invented_citations(answer)
            self._remember(text, answer)
            self.log.question(text=text, retrieval_query=trace.get("retrieval_query", text),
                              hits=hits, answer=answer, trace=trace, ship=self.ship,
                              casebook=self.casebook, air_before=air_before,
                              known=rag.all_doc_ids(self.col))
            out = f"KES: {answer}"
        self._note_citations(out)
        self.transcript.append((text, out))
        if self.ship.over:
            out += "\n\n" + self._ending()
        return out

    def handle_stream(self, text):
        """Same turn, yielded in pieces. Emits {'delta': str} as text becomes available and
        a final {'done': snapshot}."""
        text = (text or "").strip()
        if not text:
            return
        if self.ship.over:
            yield {"delta": self._ending(), "done": self.snapshot()}
            return

        full = ""
        out = self._route_local(text)
        if out is not None:
            full += out
            yield {"delta": out}
        else:
            ship_mod.advance(self.ship, ship_mod.TUNING["query_cost"])
            full += "KES: "
            yield {"delta": "KES: "}
            air_before = self.ship.air_hours
            answer, hits, trace = "", [], {}
            for piece in kes.ask_stream(self.col, text, self.state(),
                                        k=self.casebook.retrieval_k(),
                                        history=self.history, trace=trace):
                if "hits" in piece:
                    hits = piece["hits"]
                if "text" in piece:
                    answer += piece["text"]
                    full += piece["text"]
                    yield {"delta": piece["text"]}
            self._remember(text, answer)
            self.log.question(text=text, retrieval_query=trace.get("retrieval_query", text),
                              hits=hits, answer=answer, trace=trace, ship=self.ship,
                              casebook=self.casebook, air_before=air_before,
                              known=rag.all_doc_ids(self.col))

        self._note_citations(full)
        self.transcript.append((text, full))
        if self.ship.over:
            end = "\n\n" + self._ending()
            yield {"delta": end}
        yield {"done": self.snapshot()}

    def _route_local(self, text):
        """Everything that doesn't need the model. Returns a string, or None when the line
        is a question and should go to KES."""
        low = text.lower().strip()

        # 1. meta
        if low in ("quit", "exit", "q"):
            self.ship.over = True
            self.ship.ending = "quit"
            return "KES: Understood."
        if low in ("help", "?", "commands"):
            return HELP
        if low in ("status", "report", "sitrep"):
            return self._status()
        if low in ("commands", "command", "syntax", "reference"):
            return ship_mod.COMMAND_REFERENCE
        if low in ("look", "look around", "where am i", "l"):
            return self._look()
        if low in ("alerts", "alarm", "alarms"):
            alerts = self.ship.alerts()
            return "KES: " + ("\n     ".join(alerts) if alerts else "No active alerts.")

        # 2. an open verification question takes the next line as its answer
        if self.pending_verification:
            return self._check_verification(text)

        # 3. read
        m = re.fullmatch(r"read\s+(?P<doc>[a-zA-Z]+-[a-zA-Z0-9-]+)", low)
        if m:
            return self._read(m.group("doc").upper())

        # 3b. the reconstruction
        if low in ("casebook", "reconstruction", "report", "form"):
            return self._casebook_text()
        m = re.match(r"solve\s+(?P<id>[a-zA-Z]\d+)\s+(?P<rest>.+)", text.strip(), re.I)
        if m:
            answers = [a.strip() for a in re.split(r"\s*\|\s*", m.group("rest"))]
            return self._solve(m.group("id"), answers)

        # 4. ship command — exact grammar only
        air_before = self.ship.air_hours
        res = ship_mod.execute(self.ship, text)
        if res is not None:
            self.log.command(text=text, result=res, ship=self.ship,
                             casebook=self.casebook, air_before=air_before)
            body = f"KES: {res.text}"
            if res.hours:
                body += (f"\n     [{res.hours:g}h elapsed. "
                         f"{self.ship.air_hours:.1f}h of atmosphere remaining.]")
            return body

        # 5. an attempted command that didn't parse: give the syntax rather than treating
        #    a clumsy command as a question and sending it to the model
        syntax = ship_mod.near_miss(text)
        if syntax:
            self.log.near_miss(text=text, syntax=syntax)
            return ("KES: The console parser did not take that. The form it wants is:\n"
                    f"       {syntax}\n"
                    "     Type: commands  for the full vocabulary. No time lost.")

        # 6. identity claim
        if self.IDENTITY_CLAIM.search(text) and not self.ship.identified:
            self.pending_verification = self._verification_question()
            return "KES: " + self.pending_verification[2]

        # 7. not handled here: it is a question for KES
        return None

    # ------------------------------------------------------------------

    # Quoted command strings inside manuals, so procedures stand out in a wall of text.
    _CMD_IN_TEXT = re.compile(r"[\"'“‘]([a-z][a-z ]+(?:<[a-z-]+>|[a-z0-9<>. -]*))[\"'”’]")

    def _highlight(self, body):
        """Manuals bury the one line that matters — the command — in prose. Pull quoted
        command strings out visually. Done at render time so it works on corpora that are
        already generated."""
        def repl(m):
            inner = m.group(1).strip()
            if ship_mod.parse(inner) or ship_mod.near_miss(inner):
                return f"▸ {inner.upper()} ◂"
            return m.group(0)
        return self._CMD_IN_TEXT.sub(repl, body)

    def _read(self, doc_id):
        doc = rag.get_document(self.col, doc_id, tier=self.ship.tier,
                               identified=self.ship.identified)
        self.log.read(doc_id=doc_id, found=bool(doc),
                      locked=not doc and rag.document_exists(self.col, doc_id),
                      ship=self.ship, casebook=self.casebook)
        if doc:
            ship_mod.advance(self.ship, ship_mod.TUNING["read_cost"])
            return (f"--- {doc_id} ---\n{self._highlight(doc['body'])}\n--- end {doc_id} ---")
        # It matters a great deal whether this is a typo or a locked document. KES cites
        # documents it can see referenced by ID in other documents, so a player will
        # reasonably try to read things above their tier; telling them it exists but is
        # sealed turns a dead end into an objective.
        if rag.document_exists(self.col, doc_id):
            if doc_id.startswith("LOG-OKAFOR") and not self.ship.identified:
                return (f"KES: {doc_id} is in the archive, but it is a personal log and I "
                        f"cannot open it for an unidentified crew member. If you work out "
                        f"who you are, tell me.")
            return (f"KES: {doc_id} is in the archive. It is sealed above your authorization "
                    f"— command tier. The codes are in the data core, and the core is locked.")
        return (f"KES: No document {doc_id} in the archive. Check the ID, or ask me what "
                f"covers the subject and I will give you one that exists.")

    def _look(self):
        """A fixed description of the one compartment the player can reach. Hand-written,
        not generated: it has to be identical every time and it establishes the frame the
        whole game depends on — you are at a console, not walking a ship."""
        berth = ("The isolation berth is two and a half metres to your left, and occupied. "
                 "You have not looked directly at it yet.")
        pod2 = ("Pod 2 is running on battery; its status light is amber and there is a soft "
                "repeating tone you have started not to hear."
                if "F8" in self.ship.active_faults and not self.ship.wren_revived else
                "Pod 2's status light is green.")
        return "\n".join([
            "     Med bay, Deck B. Emergency lighting, a hand's width of frost on the inside",
            "     of the viewport, and your breath showing.",
            "",
            f"     {berth}",
            "",
            "     Four stasis pods along the far wall: 2, 3, 4 and 5. Pod 4 is open and empty",
            f"     and always was. {pod2}",
            "     Pod 1 stands open, its restraints unfastened from the inside.",
            "     Pod 6 is behind you, lid up, the nameplate scorched illegible. Yours.",
            "",
            "     The console you are standing at is the med bay terminal. The hatch to the",
            "     habitat ring is shut and the indicator beside it is amber: the far side is",
            "     cold. Decks A and C are further than that.",
            "",
            "     There is nothing else in here you can reach, and nothing else in here that",
            "     is moving.",
        ])

    def _casebook_text(self):
        snap = self.casebook.snapshot(self.ship.tier, self.ship.identified)
        lines = [f"RECONSTRUCTION — {snap['confirmed']} of {snap['total']} entries confirmed"
                 f"   (KES: {snap['disposition']})", ""]
        for e in snap["entries"]:
            mark = "[x]" if e["confirmed"] else "[ ]"
            seal = "  (sealed — command tier)" if e["locked"] else ""
            lines.append(f"  {mark} {e['id']}  {e['title']}{seal}")
            if e["confirmed"]:
                lines.append("        " + e["text"].format(
                    *[b["answer"] for b in e["blanks"]]))
            elif not e["locked"]:
                gaps = " | ".join(b["label"] for b in e["blanks"])
                lines.append(f"        open: {gaps}")
        lines += ["", "To fill one in:  solve <id> <answer> | <answer> | ...",
                  "Entries confirm whole. I will not tell you which blank is wrong."]
        return "\n".join(lines)

    TIERS = ["crew", "engineering", "command"]

    def _apply_grants(self, entry_id):
        """Some entries are their own unlock. Working out who you are is the identity check;
        working out the passphrase opens the core, because the entry already made the player
        type it. Tier is only ever raised, never lowered, so confirming an entry late never
        costs a player access they already had."""
        g = self.casebook.grants_for(entry_id)
        if g.get("identified"):
            self.ship.identified = True
        for flag in g.get("flags", ()):          # e.g. core_unlocked
            setattr(self.ship, flag, True)
        want = g.get("tier")
        if want and self.TIERS.index(want) > self.TIERS.index(self.ship.tier):
            self.ship.tier = want

    def _solve(self, entry_id, answers):
        ok, msg, entry = self.casebook.solve(entry_id, answers, self.ship.tier)
        if ok and entry and entry["id"] in self.casebook.confirmed:
            self._apply_grants(entry["id"])
        self.ship.full_record_available = self.casebook.can_send_full_record()
        self.log.solve(entry_id=entry_id.upper(), answers=answers, ok=ok,
                       attempts=self.casebook.attempts.get(entry_id.upper(), 0),
                       ship=self.ship, casebook=self.casebook)
        if ok:
            self._remember(f"[reconstruction entry {entry_id.upper()} confirmed]", msg)
        if ok and entry and entry["id"] in self.casebook.confirmed:
            return "KES: " + msg
        return "KES: " + msg

    def _status(self):
        r = self.ship.readings()
        lines = [f"KES: Mission Day 231, {self.ship.hours_elapsed:.1f} hours since you woke.",
                 f"     Atmosphere: {self.ship.air_hours:.1f}h remaining. "
                 f"Scrubber regeneration {r['scrubber_regen']}. Reserve air {r['reserve_air_pct']}%.",
                 f"     Reactor {r['reactor_output_pct']}%. Loop A {r['loop_a']}, loop B {r['loop_b']}.",
                 f"     Cargo hold {r['cargo_hold']}. Nav: {r['nav']}. Comms: {r['comms']}.",
                 f"     Your access: {r['access_tier']} tier."]
        alerts = self.ship.alerts()
        if alerts:
            lines.append("     Active alerts:")
            lines += [f"       - {a}" for a in alerts]
        return "\n".join(lines)

    def _ending(self):
        if not getattr(self, "_logged_end", False):
            self._logged_end = True
            self.log.end(ship=self.ship, casebook=self.casebook)
        kind = self.ship.ending or ("beacon_only" if self.ship.won else "asphyxiation")
        if kind == "quit":
            return "\n[Run abandoned.]"
        text = kes.ending(kind, self.state(), self.variant)
        extra = self.casebook.ending_lines()
        if extra and self.ship.won:
            text += "\n\n     What you worked out, for the record:\n" + "\n".join(
                f"       \u2014 {line}" for line in extra)
        return "\n" + text + "\n"

    def opening(self, arm=True):
        """arm is vestigial: there is no clock to start. Kept so the server's call still
        reads the way it did."""
        text = kes.opening(self.state())
        self._note_citations(text)
        return text

    def document(self, doc_id):
        """Fetch a document for the reader panel. Returns a dict the UI can render, with
        the same tier-aware messaging the terminal uses."""
        doc_id = doc_id.upper()
        doc = rag.get_document(self.col, doc_id, tier=self.ship.tier,
                               identified=self.ship.identified)
        if doc:
            return {"doc_id": doc_id, "meta": doc["meta"], "body": doc["body"],
                    "locked": False}
        if rag.document_exists(self.col, doc_id):
            if doc_id.startswith("LOG-OKAFOR") and not self.ship.identified:
                why = ("This is a personal log. I cannot open it for an unidentified crew "
                       "member. If you work out who you are, tell me.")
            else:
                why = ("Sealed above your authorization — command tier. The codes are in "
                       "the data core, and the core is locked.")
            return {"doc_id": doc_id, "locked": True, "body": why}
        return {"doc_id": doc_id, "locked": True, "missing": True,
                "body": "No document with that ID in the archive."}


# ---------------------------------------------------------------------------
# Terminal front end
# ---------------------------------------------------------------------------

def main():
    import argparse
    ap = argparse.ArgumentParser(description="ISV KESTREL — play in the terminal.")
    ap.add_argument("--seed", type=int, default=None,
                    help="corpus variant (default: a random one of those indexed)")
    ap.add_argument("--faults", help="comma-separated, e.g. F1,F2,F3,F5 (default: random draw)")
    ap.add_argument("--fake-embeddings", action="store_true")
    args = ap.parse_args()

    seed = args.seed
    if seed is None:
        indexed = sorted(int(p.name[1:]) for p in Path("index").glob("v*") if p.name[1:].isdigit())
        if not indexed:
            raise SystemExit("No index found. Run: python build_index.py --seed 1")
        seed = random.choice(indexed)

    faults = set(args.faults.upper().split(",")) if args.faults else None
    s = Session(seed=seed, faults=faults, fake_embeddings=args.fake_embeddings)

    print("\n" + "=" * 72)
    print("  ISV KESTREL".center(72))
    print("=" * 72 + "\n")
    print(s.opening())
    print("\n(type 'help' for the interface, 'quit' to give up)\n")

    while not s.ship.over:
        try:
            line = input("> ")
        except (EOFError, KeyboardInterrupt):
            print()
            break
        out = s.handle(line)
        if out:
            print("\n" + out + "\n")

    if s.ship.over and s.ship.ending != "quit":
        print()


if __name__ == "__main__":
    main()
