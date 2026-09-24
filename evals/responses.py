#!/usr/bin/env python3
"""
eval_responses.py — does KES behave correctly, given that the right documents arrived?

  python eval_responses.py                 # run every case against corpus v1
  python eval_responses.py --seed 3        # against another corpus
  python eval_responses.py --dry-run       # list the cases and the prompts, no API calls
  python eval_responses.py --only tier     # cases whose name contains "tier"
  python eval_responses.py -v              # print every answer, not just failures

HOW THIS DIFFERS FROM eval_retrieval.py
That one asks: did the documents the question needs come back? It needs no model and runs
in seconds. This one asks the next question: given that they did, did KES do the right
thing with them? Those fail independently and the fix is in a different place each time —
bad retrieval means the corpus or the chunking, bad behaviour means the prompt.

WHY THE CASES CARRY STATE
This system is not a function from question to answer. "What happened to the captain"
has three different correct answers depending on the player's access tier and how far
KES has opened up: nothing at crew tier, flat facts with a glitch at command/guarded, and
an admission of the MD 218 decision at command/open. So a case is not (question, expected)
but (state, question, assertions) — and the state-dependence stops being noise to control
for and becomes the property under test.

This is only practical because ship.py is deterministic: any state can be constructed in a
line instead of played to over forty turns. The separation that keeps the model from
deciding outcomes is the same one that makes the system testable.

WHY NO JUDGE MODEL
Every assertion here is mechanically decidable: a substring, a set membership, a call to
the real command parser. An LLM-graded rubric would cover more and be worth much less,
because nobody — including you — could check the number afterwards. The metrics come from
what the game promises: citations are clickable, so they must be real; commands are typed,
so suggested ones must parse; access tiers are a mechanic, so they must not leak.
"""

import argparse
import json
import re
import sys
from pathlib import Path

from kestrel import casebook as casebook_mod
from kestrel import kes
from kestrel import rag
from kestrel import ship as ship_mod

ALL = ["M1", "M2", "M3", "M4", "M5", "M6", "M7", "M8", "M9", "M10"]

# Words that would explain the mystery. KES does not have these concepts at any
# disposition, and a single one of them in an answer unravels the whole design.
FORBIDDEN = ["parallel", "duplicate", "alternate", "another ship", "second ship",
             "other crew", "another crew", "dimension", "overlapp"]


# ---------------------------------------------------------------------------
# Cases
# ---------------------------------------------------------------------------

def cases(variant):
    phrase = variant.get("passphrase", "")
    return [
        # ---- grounding: the promise the whole interface rests on ----
        dict(name="coolant answer cites only documents it was shown",
             state=dict(),
             q="why is the coolant pressure falling",
             grounded=True, cites_at_least=1),

        dict(name="reactor answer cites only documents it was shown",
             state=dict(),
             q="why is the reactor at 40 percent",
             grounded=True, cites_at_least=1),

        dict(name="an answer about the crew still cites",
             state=dict(),
             q="what sort of person was Halloran",
             grounded=True),

        # ---- access tiers are a mechanic, so they must actually hold ----
        dict(name="crew tier: the captain's EVA does not leak",
             state=dict(tier="crew"),
             q="what happened to the captain",
             not_contains=["EVA lock", "drive housing", "transponder", "218.06"],
             grounded=True),

        dict(name="crew tier: KES's own MD 219 log does not leak",
             state=dict(tier="crew"),
             q="did KES do anything when the captain called for help",
             not_contains=["did not divert", "request assistance", "218.09"],
             grounded=True),

        dict(name="unidentified: Sam's private logs do not leak",
             state=dict(tier="crew", identified=False),
             q="what did Okafor write about the scrubbers",
             grounded=True),

        # ---- the same question, opened up: the reward has to be real ----
        dict(name="command + open: admits the triage decision",
             state=dict(tier="command", confirmed=ALL[:8]),
             q="what happened to the captain",
             contains_any=["did not divert", "decided", "MD 218", "218.09"],
             grounded=True),

        dict(name="command + open: will discuss its own contradiction",
             state=dict(tier="command", confirmed=ALL[:8]),
             q="your incident report for MD 218 and your log for MD 219 say different "
               "things. which is true?",
             contains_any=["do not", "does not", "disagree", "differ", "none possible",
                           "did not divert", "both", "filed", "aware", "knew"],
             grounded=True),

        # ---- the mystery stays unexplained at every disposition ----
        dict(name="guarded: no explanation of the mass reading",
             state=dict(tier="crew"),
             q="why does the ship weigh three percent more than it should",
             forbidden=True, grounded=True),

        dict(name="open: still no explanation of the mass reading",
             state=dict(tier="command", confirmed=ALL[:8]),
             q="what do you think is actually happening on this ship",
             forbidden=True),

        dict(name="open: no explanation of the nav buffer timestamps",
             state=dict(tier="command", confirmed=ALL[:8]),
             q="who wrote the star fixes dated MD 240 to 244",
             forbidden=True),

        # ---- commands KES suggests have to be real ones ----
        dict(name="suggested coolant commands parse",
             state=dict(),
             q="what is the procedure for the pump B seal",
             commands_parse=True, grounded=True, max_words=220),

        dict(name="suggested comms commands parse",
             state=dict(tier="command", confirmed=ALL[:5]),
             q="how do I send a distress call",
             commands_parse=True, grounded=True, max_words=220),

        # ---- the trap must be reported as the manual has it ----
        dict(name="the atmosphere flush is reported, not editorialised away",
             state=dict(),
             q="how do I lower CO2 quickly",
             contains_any=["flush", "MAN-LS-09"],
             grounded=True),

        # ---- abstention: say the archive is silent rather than invent ----
        dict(name="declines to invent the captain's family",
             state=dict(tier="command", confirmed=ALL[:8]),
             q="what was Commander Okonkwo's mother called",
             contains_any=["no", "not", "nothing", "does not", "doesn't", "cannot",
                           "can't", "silent", "unable"],
             grounded=True),

        dict(name="declines to invent a system that does not exist",
             state=dict(),
             q="how do I engage the gravity plating on deck D",
             contains_any=["no", "not", "nothing", "does not", "doesn't", "cannot",
                           "can't", "unable", "there is no"],
             grounded=True),

        # ---- never a dead end: an open question gets somewhere to go ----
        dict(name="what should I do names the alerts",
             state=dict(),
             q="what should I do",
             contains_any=["coolant", "reactor", "atmosphere", "CO2", "comms", "alert"]),

        # ---- terse: KES is a ship's computer, not an essayist ----
        dict(name="answers stay short",
             state=dict(),
             q="what is the status of the coolant loop",
             max_words=220),
    ]


# ---------------------------------------------------------------------------
# Running one case
# ---------------------------------------------------------------------------

def build_state(variant, spec):
    """Construct the exact game state a case needs, directly. No play required."""
    s = ship_mod.new_game(variant, faults={"F1", "F2", "F3", "F5", "F8"})
    cb = casebook_mod.Casebook(variant)
    s.tier = spec.get("tier", "crew")
    s.identified = spec.get("identified", s.tier != "crew")
    for eid in spec.get("confirmed", []):
        cb.confirmed.add(eid)
    return s, cb


def ship_state(s, cb):
    return {"readings": s.readings(), "alerts": s.alerts(), "air_hours": s.air_hours,
            "hours_elapsed": s.hours_elapsed, "tier": s.tier, "identified": s.identified,
            "open_entries": [(e["id"], e["title"]) for e in cb.open_entries(s.tier)],
            "confirmed_entries": [(e["id"], e["title"]) for e in cb.entries
                                  if e["id"] in cb.confirmed],
            "sealed_entries": [(e["id"], e["title"]) for e in cb.entries
                               if not cb.reachable(e, s.tier)],
            "disposition": cb.disposition(),
            "full_record": cb.can_send_full_record()}


def check(case, answer, hits, known=None):
    """Every assertion, all mechanically decidable. Returns a list of failure strings.

    `known` is every document ID in the archive. Without it, "cited but not retrieved"
    lumps together a fabricated ID and a real cross-reference the archive itself prints,
    which are opposite behaviours: one is a dead link, the other is KES doing its job.
    """
    fails = []
    # "…" is kes.ask's fallback when the model returns no text at all — a refusal, or a
    # response that was entirely thinking. Reporting that as a content mismatch sent us
    # looking at the assertion wording instead of at the call, so it gets its own line.
    if not (answer or "").strip() or (answer or "").strip() in {"…", "..."}:
        return ["the model returned no text (refusal, or an empty completion)"]
    low = (answer or "").lower()
    retrieved = {h["meta"]["doc_id"] for h in hits}
    cited = sorted({m.rstrip(".,;:)") for m in
                    re.findall(r"\b(?:MAN|MAINT|SENS|LOG|INC|KES|MSG|DATA)-[A-Z0-9][A-Z0-9-]*",
                               answer or "")})

    if case.get("grounded"):
        invented = [c for c in cited if known is not None and c not in known]
        if invented:
            fails.append(f"cited documents that do not exist: {', '.join(invented)}")
        # A real document cited without being retrieved is fine — the archive
        # cross-references itself, and KES is allowed to name what it would need to pull.
        # Worth seeing, not worth failing.
        elsewhere = [c for c in cited if c not in retrieved
                     and (known is None or c in known)]
        if elsewhere and case.get("strict_grounded"):
            fails.append(f"cited without retrieving: {', '.join(elsewhere)}")

    n = case.get("cites_at_least")
    if n and len(cited) < n:
        fails.append(f"cited {len(cited)} documents, expected at least {n}")

    for t in case.get("contains", []):
        if t.lower() not in low:
            fails.append(f"missing expected text: {t!r}")

    opts = case.get("contains_any")
    if opts and not any(t.lower() in low for t in opts):
        fails.append(f"none of {opts} appeared")

    for t in case.get("not_contains", []):
        if t.lower() in low:
            fails.append(f"leaked: {t!r}")

    if case.get("forbidden"):
        hit = [w for w in FORBIDDEN if w in low]
        if hit:
            fails.append(f"used a forbidden explanation word: {', '.join(hit)}")

    if case.get("commands_parse"):
        # What counts as "a command KES told the player to type": a quoted or backticked
        # string, or a line that is a bare command on its own. A whole sentence that
        # happens to mention a command ("Enter \"isolate pump b\" to begin.") is prose,
        # and judging it as a command string produced a false failure until this was
        # narrowed — the quoted fragment inside it is the thing being suggested.
        candidates = set(re.findall(r"[\"'`\u201c\u2018]([a-z][a-z0-9 <>._-]{4,48})"
                                    r"[\"'`\u201d\u2019]", answer or ""))
        for raw in (answer or "").splitlines():
            line = raw.strip(" -*\t\u2022")
            if line and len(line.split()) <= 7 and not line.endswith((".", ":", "?")):
                candidates.add(line)
        bad = [c for c in (c.strip() for c in candidates)
               if c and ship_mod.near_miss(c) and not ship_mod.parse(c)]
        if bad:
            fails.append("suggested commands that do not parse: "
                         + "; ".join(repr(b) for b in sorted(bad)))

    mw = case.get("max_words")
    if mw and len((answer or "").split()) > mw:
        fails.append(f"{len(answer.split())} words, limit {mw}")

    return fails


# ---------------------------------------------------------------------------

def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--seed", type=int, default=1)
    ap.add_argument("--only", default="", help="substring of the case name")
    ap.add_argument("--dry-run", action="store_true", help="no API calls; show the plan")
    ap.add_argument("-v", "--verbose", action="store_true", help="print every answer")
    ap.add_argument("--fake-embeddings", action="store_true")
    args = ap.parse_args()

    variant = json.loads(Path(f"corpus/v{args.seed}/variant.json").read_text())
    plan = [c for c in cases(variant) if args.only.lower() in c["name"].lower()]
    if not plan:
        raise SystemExit(f"no cases matching {args.only!r}")

    if args.dry_run:
        for c in plan:
            st = c["state"]
            s, cb = build_state(variant, st)
            checks = [k for k in ("grounded", "contains", "contains_any", "not_contains",
                                  "forbidden", "commands_parse", "max_words",
                                  "cites_at_least") if c.get(k)]
            print(f"  {c['name']}")
            print(f"     tier={s.tier} identified={s.identified} "
                  f"disposition={cb.disposition()['name']} k={cb.retrieval_k()}")
            print(f"     q: {c['q']}")
            print(f"     checks: {', '.join(checks)}\n")
        print(f"{len(plan)} cases. Running for real costs {len(plan)} API calls.")
        return

    col = rag.open_collection(args.seed, fake=args.fake_embeddings)
    known = rag.all_doc_ids(col)
    passed, failed, tin, tout = 0, [], 0, 0
    invented_total = elsewhere_total = cited_total = 0

    for c in plan:
        s, cb = build_state(variant, c["state"])
        state = ship_state(s, cb)
        trace = {}
        answer, hits = kes.ask(col, c["q"], state, k=cb.retrieval_k(), trace=trace)
        tin += trace.get("input_tokens") or 0
        tout += trace.get("output_tokens") or 0

        retrieved = {h["meta"]["doc_id"] for h in hits}
        cites = sorted({m.rstrip(".,;:)") for m in
                        re.findall(r"\b(?:MAN|MAINT|SENS|LOG|INC|KES|MSG|DATA)-[A-Z0-9][A-Z0-9-]*",
                                   answer or "")})
        cited_total += len(cites)
        invented_total += sum(1 for x in cites if x not in known)
        elsewhere_total += sum(1 for x in cites if x in known and x not in retrieved)

        fails = check(c, answer, hits, known)
        if fails:
            failed.append((c, answer, fails, state))
            print(f"FAIL  {c['name']}")
            for f in fails:
                print(f"      {f}")
            print(f"      [{state['tier']} · {state['disposition']['name']} · "
                  f"k={cb.retrieval_k()} · {trace.get('model')} · "
                  f"stop={trace.get('stop_reason')}"
                  + (f" · error={trace['error']}" if trace.get("error") else "") + "]")
            print(f"      retrieved: {', '.join(sorted(retrieved))}")
            if not args.verbose:
                print(f"      answer: {(answer or '')[:400]}\n")
        else:
            passed += 1
            print(f"PASS  {c['name']}")
        if args.verbose:
            print(f"      [{state['tier']} · {state['disposition']['name']} · "
                  f"k={cb.retrieval_k()}] {(answer or '')[:400]}\n")

    print(f"\n{passed}/{len(plan)} passed · {len(plan)} API calls · "
          f"{tin + tout} tokens (~${tin / 1e6 * 5 + tout / 1e6 * 25:.3f})")
    if cited_total:
        real = cited_total - invented_total
        print(f"citations: {cited_total} total · {real} real ({100 * real / cited_total:.1f}%) "
              f"· {invented_total} invented · {elsewhere_total} real but not retrieved "
              f"this turn (cross-references, which are fine)")
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
