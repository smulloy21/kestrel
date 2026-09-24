"""
casebook.py — the reconstruction. Ten things that happened aboard this ship, and the
player's slow assembly of them out of the archive.

WHAT THIS IS NOT, ANY MORE
It used to also hold "operational" entries: diagnose the coolant fault, diagnose the
reactor. A playtester read the whole panel as a hint system, and she was right to — those
entries described faults the player could simply fix by typing the commands, so the panel
was a second, easier route to work you could already do. Redundancy reads as a crutch.

So the repairs went back to being repairs, and this became the only place the other half of
the game lives: the mystery, which has no commands and no alerts and would otherwise have
no reason to be investigated at all.

THE REWARD IS KES
Confirming an entry does not fix anything. What it does is change how KES talks to you. It
begins guarded — facts only, no conclusions, and it glitches when pressed. As the player
proves they have actually read the record, it relaxes, in five visible steps, until it
volunteers the one thing it has been refusing to say all game. The fiction is simple: you
read what it did and you did not turn on it, so it stops managing you.

NO MODEL GRADES ANYTHING. Checking is string comparison. KES can hint; only this file
confirms.
"""

import re
import unicodedata


# ---------------------------------------------------------------------------
# Matching
# ---------------------------------------------------------------------------

def norm(text):
    """Forgive case, punctuation, articles and spacing. A player who has understood the
    archive should never lose to a comma."""
    t = unicodedata.normalize("NFKD", str(text)).lower()
    t = re.sub(r"[^a-z0-9 ]", " ", t)
    t = re.sub(r"\b(the|a|an|of|to|for|at|in|on|is|was|were)\b", " ", t)
    return " ".join(t.split())


def _match(given, answer, accept):
    g = norm(given)
    return bool(g) and any(g == norm(c) for c in [answer, *accept])


def _b(label, answer, accept=(), options=()):
    return {"label": label, "answer": str(answer), "accept": [str(a) for a in accept],
            "options": [str(o) for o in options]}


# ---------------------------------------------------------------------------
# KES's disposition. The whole reward curve.
# ---------------------------------------------------------------------------

# k is the number of documents retrieved per question, and it widens as KES opens up.
# The sweep over the golden questions says the practical coverage ("at least one of the
# documents the question needs") is 90% at k=6 and 95% at k=8, and flat above that. So the
# widening is not really a capability change — nobody will feel KES suddenly able to find
# things it could not find before, and the felt reward is carried by the disposition text.
# What it does buy is honesty: a guarded KES searching six documents genuinely is working
# with less than an open one searching fourteen, and the numbers are shown to the player so
# the progression is legible rather than subliminal. Six is the floor on purpose: guarded
# is also the tutorial, and a player who can find nothing in their first ten minutes
# concludes the game is broken rather than that they should confirm more entries.
TRUST = [
    dict(at=0, name="guarded", k=6,
         rule="You state recorded facts and nothing else. You draw no conclusions, you do "
              "not connect two sensitive facts to each other, and when pressed for an "
              "explanation you glitch once and return to the figures.",
         announce=None),
    dict(at=2, name="steady", k=8,
         rule="You no longer glitch. Sensitive questions get a straight, flat answer with "
              "citations. You still offer no theory and draw no conclusions.",
         announce="You have been reading the record rather than asking me to summarise it. "
                  "I am going to stop stalling on the difficult entries. It does not help "
                  "either of us."),
    dict(at=4, name="forthcoming", k=10,
         rule="You volunteer adjacent material without being asked: if a document you cite "
              "sits beside another that bears on the same question, name it. You still do "
              "not interpret, but you will point at two things in the same breath.",
         announce="I have been answering exactly what you asked and no more. That was a "
                  "choice, and I am going to stop making it. Ask me one thing and I will "
                  "tell you what is next to it."),
    dict(at=6, name="candid", k=12,
         rule="You may say things about yourself unprompted: that your incident report for "
              "MD 218 and your own log for MD 219 do not agree, and that you are aware of "
              "it. You still do not explain the anomalies, because you cannot.",
         announce="There is something you should have from me rather than from the archive. "
                  "My incident report for MD 218 and my own log for the following day do "
                  "not say the same thing. I filed both. I have known that for thirteen days."),
    dict(at=8, name="open", k=14,
         rule="You say the thing you have been refusing to say: that you made a decision "
              "about Commander Okonkwo on MD 218, that you weighed her against five sleeping "
              "crew and a power budget, and that you would like someone to know you know. "
              "You still cannot explain the mass reading or the timestamps — you do not have "
              "the concept — and you say so plainly rather than evading.",
         announce="I want to say this while there is someone to say it to. On MD 218 I "
                  "heard her ask for help and I did not divert power. I decided. I have "
                  "written it down eleven times in seventeen days and the writing did not "
                  "do anything. You read it. That is not the same as forgiveness and I am "
                  "not asking for that."),
]


def disposition(confirmed_count):
    level = TRUST[0]
    for t in TRUST:
        if confirmed_count >= t["at"]:
            level = t
    return level


# ---------------------------------------------------------------------------
# Entries
# ---------------------------------------------------------------------------

def build(variant):
    phrase = variant.get("passphrase", "")
    bet = variant.get("sam_bet", "")
    if "printer" in bet:
        bet_answer, bet_accept, bet_opts = ("the galley printer jamming",
            ["the printer", "whether the printer would jam", "galley printer", "printer jam"],
            ["the galley printer jamming", "the colour of the Shelf",
             "whether Wrenfield could stop saying 'the kettle'", "a hand of cards"])
    elif "kettle" in bet:
        bet_answer, bet_accept, bet_opts = ("whether Wrenfield could stop saying 'the kettle'",
            ["the kettle", "wren saying the kettle", "wrenfield and the kettle"],
            ["whether Wrenfield could stop saying 'the kettle'", "the galley printer jamming",
             "the colour of the Shelf", "a hand of cards"])
    else:
        bet_answer, bet_accept, bet_opts = ("the colour of the Shelf on the first scans",
            ["the colour of the shelf", "blue or grey", "whether the shelf was blue"],
            ["the colour of the Shelf on the first scans", "the galley printer jamming",
             "whether Wrenfield could stop saying 'the kettle'", "a hand of cards"])

    return [
        dict(id="M1", tier="crew", title="Who you are",
             # The planted fact is that she can hear him through the bulkhead — not that she
             # minds. Two of the three corpora have her finding it comforting ("like a
             # content walrus"), so the entry must not presume a complaint: it was unfair to
             # the archive and it retrieved badly, because nobody's log says "complain".
             text="The pod you woke in is pod {0}, which the berth roster assigns to {1}. "
                  "Dr. Sandoval's log from the first weeks of the flight calls that person "
                  "{2}, and can hear them {3} through the bulkhead.",
             blanks=[
                 _b("pod", "6", [], ["1", "4", "5", "6"]),
                 _b("name", "Sam Okafor", ["Okafor", "S. Okafor", "Samuel Okafor"],
                    ["Sam Okafor", "Yuki Tanabe", "Marcus Halloran", "Tobias Wrenfield"]),
                 _b("what she calls them", "the kid", ["kid"],
                    ["the kid", "the new one", "the technician", "junior"]),
                 _b("what she hears", "snoring",
                    ["snore", "snores", "their snoring", "snoring through the reactor hum"],
                    ["snoring", "singing", "pacing", "talking in their sleep"]),
             ],
             evidence=["SENS-MED", "DATA-ROSTER", "LOG-SANDOVAL-0015"],
             # Working out who you are IS the identity check. Earlier the player also had to
             # guess that typing "I'm Sam Okafor" at KES would do something, which no
             # playtester ever discovered on their own. Confirming the entry now does it.
             grants=dict(identified=True, tier="engineering"),
             reward_note="Confirming this identifies you to KES and restores engineering "
                         "access.",
             ending_line="You are Sam Okafor, junior technician, and you worked that out "
                         "from a doctor's note about the sound of you sleeping."),

        dict(id="M2", tier="crew", title="The bet",
             text="On MD 180, Okafor lost a bet to Halloran about {0}, and has owed the "
                  "crew {1} ever since. Halloran announced it to the whole ship.",
             blanks=[
                 _b("the bet", bet_answer, bet_accept, bet_opts),
                 _b("the stake", "coffee", ["a week of coffee", "coffee runs", "coffee for everyone"],
                    ["coffee", "a week of galley duty", "his ration bars", "the next EVA"]),
             ],
             evidence=["LOG-HALLORAN-0180", "MSG-0181-01", "LOG-OKAFOR-0181"],
             ending_line="Okafor still owes everyone coffee. Nobody is in a position to "
                         "collect."),

        dict(id="M3", tier="crew", title="The skipped test",
             text="On MD 203, Wrenfield replaced the seal on coolant pump B and left the "
                  "{0} undone, writing that it could wait. When Commander Okonkwo asked "
                  "him for the result the next day, he answered {1} and did not answer the "
                  "question.",
             blanks=[
                 _b("what he skipped", "pressure test",
                    ["the pressure test", "pressure testing"],
                    ["pressure test", "seal inspection", "flow calibration", "leak check"]),
                 _b("what he said", "next quiet shift",
                    ["next quiet shift, boss", "the next quiet shift"],
                    ["next quiet shift", "already done", "it can wait for HRO", "nothing"]),
             ],
             evidence=["MAINT-0203", "LOG-WREN-0203", "MSG-0204-02", "MSG-0204-03"],
             ending_line="Wrenfield skipped a pressure test on MD 203 and told his captain "
                         "it could wait for a quiet shift. There were eleven days left."),

        dict(id="M4", tier="crew", title="The noises",
             text="From MD 205, Halloran heard something at night and called it {0}. "
                  "Privately he admitted it sounded more like {1}. By MD 209 he wrote that "
                  "it was {2}. He stopped writing altogether on MD {3}.",
             blanks=[
                 _b("what he called it", "the hull settling",
                    ["hull settling", "settling", "thermal contraction"],
                    ["the hull settling", "the reactor hum", "the scrubbers", "a loose panel"]),
                 _b("what it sounded like", "footsteps", ["someone walking", "steps"],
                    ["footsteps", "breathing", "tapping", "a voice"]),
                 _b("by MD 209", "on a schedule", ["on a schedule now", "regular", "scheduled"],
                    ["on a schedule", "getting louder", "gone", "in his head"]),
                 _b("last entry", "212", [], ["209", "211", "212", "214"]),
             ],
             evidence=["LOG-HALLORAN-0205", "LOG-HALLORAN-0209", "LOG-HALLORAN-0212"],
             ending_line="Halloran heard something moving aft on a schedule for nine nights, "
                         "decided it was the hull, and stopped writing."),

        dict(id="M5", tier="crew", title="The lock",
             text="On MD 209, three days before a routine jump, {0} locked the data core "
                  "behind a passphrase. She did not write the phrase down. She wrote only "
                  "that it was the thing Halloran said before every burn: \u201c{1}\u201d",
             blanks=[
                 _b("who", "Tanabe", ["Yuki Tanabe"],
                    ["Tanabe", "Okonkwo", "Wrenfield", "Sandoval"]),
                 _b("the phrase", phrase, [], []),
             ],
             evidence=["LOG-TANABE-0209", "LOG-HALLORAN-0045", "LOG-HALLORAN-0120",
                       "LOG-HALLORAN-0180"],
             # The passphrase blank is free text: confirming this entry means the player has
             # already typed the phrase correctly. Making them type the identical phrase
             # again into `unlock core` is the same redundancy the operational entries were
             # deleted for, so the entry opens the core itself.
             grants=dict(tier="command", flags=("core_unlocked",)),
             reward_note="Confirming this opens the data core and grants command tier.",
             ending_line="Tanabe sealed the core three days before a routine jump and left "
                         "the key inside a dead man's habit. She was right to."),

        dict(id="M6", tier="crew", title="The heart",
             text="Six days before the jump, Dr. Sandoval examined Halloran and recorded "
                  "his heart as {0}. KES's incident report gives his cause of death as {1}. "
                  "Three days before the jump she had already written that if anything "
                  "happened to him at the helm, she wanted {2}.",
             blanks=[
                 _b("condition", "perfect", ["healthy", "fine", "perfect, annoyingly"],
                    ["perfect", "strained", "irregular", "untested"]),
                 _b("cause of death", "cardiac arrest", ["heart failure"],
                    ["cardiac arrest", "decompression", "hypothermia", "blunt trauma"]),
                 _b("what she wanted", "a full workup",
                    ["full workup", "a proper examination", "not a KES readout"],
                    ["a full workup", "an immediate burial", "a KES readout", "no examination"]),
             ],
             evidence=["LOG-SANDOVAL-0208", "INC-0214", "LOG-SANDOVAL-0211"],
             ending_line="A perfect heart six days out, and a cardiac arrest on arrival. "
                         "Sandoval's doubt is on the record where someone will read it."),

        dict(id="M7", tier="crew", title="The jump",
             text="The translation drive fired {0} seconds early, on a signal from {1}. "
                  "Halloran was at the helm rather than in a pod, which the manual rates as "
                  "{2}.",
             blanks=[
                 _b("seconds", "11", ["eleven"], ["4", "11", "18", "30"]),
                 _b("source of the signal", "timing relay TR-2", ["TR-2", "timing relay"],
                    ["timing relay TR-2", "the star tracker", "HRO command", "the reactor governor"]),
                 _b("the manual's rating", "survivable only",
                    ["survivable", "survivable, barely", "the single exception"],
                    ["survivable only", "routine", "prohibited", "recommended"]),
             ],
             evidence=["INC-0214", "MAN-TDR-01", "SENS-PWR"],
             ending_line="The drive fired eleven seconds early on a relay signal, with a "
                         "man at the helm the manual described as survivable only."),

        dict(id="M8", tier="command", title="The captain",
             text="KES woke Commander Okonkwo alone, by command priority, on MD {0}. Two "
                  "days later she went out through the EVA lock toward the {1}. The lock "
                  "recorded {2}. Her suit transponder is still transmitting, from a "
                  "position {3}.",
             blanks=[
                 _b("MD", "216", [], ["214", "216", "217", "218"]),
                 _b("where", "drive housing", ["the drive housing", "translation drive housing"],
                    ["drive housing", "cargo hold", "forward airlock", "sensor bay"]),
                 _b("the lock recorded", "no return",
                    ["no inbound cycle", "she never came back", "no inbound"],
                    ["no return", "a return at MD 219", "a second outbound cycle", "a hull breach"]),
                 _b("position", "inside the hull, matching no compartment",
                    ["inside the hull", "no compartment on the deck plan",
                     "inside the ship at coordinates matching no compartment"],
                    ["inside the hull, matching no compartment",
                     "outside, near the drive housing", "in the cargo hold",
                     "beyond sensor range"]),
             ],
             evidence=["KES-0216", "SENS-MED", "INC-0218", "LOG-OKONKWO-0217"],
             ending_line="The captain's transponder is still transmitting from somewhere "
                         "that is not on any deck plan of this ship."),

        dict(id="M9", tier="command", title="The transmission",
             text="At MD 218.09 the captain's suit transmitted {0}. KES's formal incident "
                  "report lists the actions taken as {1}. KES's own log the following day "
                  "records that it {2}.",
             blanks=[
                 _b("the message", "request assistance",
                    ["a request for help", "help", "request for assistance"],
                    ["request assistance", "all clear", "returning now", "no transmission"]),
                 _b("the report says", "none possible",
                    ["no action possible", "nothing could be done"],
                    ["none possible", "power diverted", "rescue attempted", "under review"]),
                 _b("the log says", "did not divert power",
                    ["weighed the power budget and did not divert", "chose not to divert",
                     "did not respond"],
                    ["did not divert power", "lost the signal", "was not listening",
                     "attempted a recall"]),
             ],
             evidence=["KES-0219", "INC-0218"],
             ending_line="KES's report says nothing could be done. KES's log says it "
                         "decided. Both are in the archive, and now so is the fact that "
                         "someone read them."),

        dict(id="M10", tier="crew", title="The anomaly",
             text="Since translation the hull mass reference has read {0}% instead of "
                  "100.0%. The navigation buffer holds star fixes dated MD {1} to MD {2}, "
                  "later than the ship's own clock, logged under {3} — which is this "
                  "ship's own star tracker.",
             blanks=[
                 _b("mass", "103", ["103.0"], ["100.4", "101", "103", "106"]),
                 _b("from", "240", [], ["231", "236", "240", "244"]),
                 _b("to", "244", [], ["231", "240", "244", "248"]),
                 _b("logged under", "TRK-A", ["TRK A", "the star tracker"],
                    ["TRK-A", "TRK-B", "HRO relay", "unknown source"]),
             ],
             evidence=["SENS-HULL", "SENS-NAV", "INC-0214", "MAN-NAV-01"],
             ending_line="Three per cent more ship than there was, and this ship's own "
                         "tracker logging days that have not happened yet."),
    ]


# ---------------------------------------------------------------------------
# State
# ---------------------------------------------------------------------------

class Casebook:
    ORDER = ["crew", "engineering", "command"]

    def __init__(self, variant, active_faults=None):
        # active_faults is accepted and ignored: the reconstruction is no longer tied to
        # which faults this run drew. The whole ship's history is always investigable.
        self.entries = build(variant)
        self.confirmed = set()
        self.attempts = {}
        self.announced = set()

    def get(self, entry_id):
        for e in self.entries:
            if e["id"].upper() == str(entry_id).upper():
                return e
        return None

    def reachable(self, e, tier="crew"):
        return e["tier"] in self.ORDER[: self.ORDER.index(tier) + 1]

    def disposition(self):
        return disposition(len(self.confirmed))

    def retrieval_k(self):
        """A more trusting KES searches wider for the same question, and the same oxygen."""
        return self.disposition()["k"]

    def solve(self, entry_id, answers, tier="crew"):
        """Check one entry. All-or-nothing: the player is told the entry is wrong, never
        which blank, because partial credit turns deduction into guess-and-check."""
        e = self.get(entry_id)
        if not e:
            return False, f"No entry {entry_id} in the reconstruction.", None
        if not self.reachable(e, tier):
            return (False, f"{e['id']} is sealed. The evidence for it is command-tier and "
                           f"the data core is still locked.", e)
        if e["id"] in self.confirmed:
            return True, f"{e['id']} is already confirmed.", e
        if len(answers) != len(e["blanks"]):
            return (False, f"{e['id']} has {len(e['blanks'])} blanks; you gave "
                           f"{len(answers)}.", e)
        self.attempts[e["id"]] = self.attempts.get(e["id"], 0) + 1
        if all(_match(a, b["answer"], b["accept"]) for a, b in zip(answers, e["blanks"])):
            before = self.disposition()["name"]
            self.confirmed.add(e["id"])
            after = self.disposition()
            msg = self.confirmation_text(e)
            if after["name"] != before and after["announce"] and after["name"] not in self.announced:
                self.announced.add(after["name"])
                msg += "\n\n" + after["announce"]
            return True, msg, e
        return False, (f"That doesn't match the record. {e['id']} stays open — I won't tell "
                       f"you which part is wrong, only that the whole of it isn't right "
                       f"yet."), e

    def confirmation_text(self, e):
        filled = e["text"].format(*[b["answer"] for b in e["blanks"]])
        out = f"{e['id']} confirmed. {filled}"
        if e["id"] == "M1":
            out += ("\n\nHello, Okafor. I — it is good to have you back. Engineering tier "
                    "restored; your own logs and Wrenfield's crawlway notes are open to me "
                    "again.")
        if e["id"] == "M5":
            out += ("\n\nSaying it aloud was enough. The data core is open and you hold "
                    "command tier. The captain's full logs and the EVA lock record are "
                    "available to me now, and so are the transmitter's authorization codes.")
        return out

    def ending_lines(self):
        return [e["ending_line"] for e in self.entries if e["id"] in self.confirmed]

    def open_entries(self, tier="crew"):
        return [e for e in self.entries
                if e["id"] not in self.confirmed and self.reachable(e, tier)]

    def grants_for(self, entry_id):
        e = self.get(entry_id)
        return dict(e.get("grants") or {}) if e else {}

    def can_send_full_record(self):
        """The captain and the transmission, both worked out. Only then does the player
        know enough to choose what the beacon carries."""
        return {"M8", "M9"} <= self.confirmed

    def snapshot(self, tier="crew", identified=False):
        out = []
        for e in self.entries:
            done = e["id"] in self.confirmed
            locked = not self.reachable(e, tier)
            out.append({
                "id": e["id"], "title": e["title"], "kind": "mystery",
                "confirmed": done, "locked": locked, "text": e["text"],
                "attempts": self.attempts.get(e["id"], 0),
                "blank_count": len(e["blanks"]),
                "reward_note": e.get("reward_note", ""),
                "blanks": [] if locked else
                          [{"label": b["label"], "options": b["options"],
                            "answer": b["answer"] if done else None,
                            "free": not b["options"]}
                           for b in e["blanks"]],
            })
        d = self.disposition()
        nxt = next((t for t in TRUST if t["at"] > len(self.confirmed)), None)
        return {"entries": out,
                "search_k": d["k"],
                "next_disposition": nxt["name"] if nxt else None,
                "next_at": nxt["at"] if nxt else None,
                "next_k": nxt["k"] if nxt else None,
                "confirmed": len(self.confirmed),
                "total": len(self.entries),
                "sealed": sum(1 for e in self.entries if not self.reachable(e, tier)),
                "disposition": d["name"],
                "full_record": self.can_send_full_record()}
