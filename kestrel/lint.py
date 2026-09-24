#!/usr/bin/env python3
"""
lint_corpus.py — sanity checks on a generated corpus before you read it by hand.

  python lint_corpus.py            # checks corpus/v1
  python lint_corpus.py --seed 7   # checks corpus/v7

Reports, per document:
  NAMES     capitalized words that look like a person's name but aren't on the roster
  FORBIDDEN mystery-explaining words the world rules ban
  FUTURE    "MD <n>" references later than the document's own date
  COMMANDS  fault documents whose body doesn't contain the player command it should teach
  FENCES    stray markdown code fences
Nothing here is authoritative; it's a list of places to look. Exit code 1 if anything fired.
"""

import argparse
import json
import re
import sys
from pathlib import Path

from . import bible

ROSTER = {"Adaeze", "Okonkwo", "Tobias", "Wren", "Wrenfield", "Priya", "Sandoval",
          "Marcus", "Halloran", "Yuki", "Tanabe", "Sam", "Okafor", "KES", "HRO"}
FORBIDDEN = ["parallel", "overlap", "duplicate", "another ship", "other crew", "alternate",
             "dimension", "other kestrel", "second kestrel", "another crew"]
# Words that are capitalized for reasons other than being a name.
NOISE = {"Kestrel", "Deck", "Cabin", "Pod", "Shelf", "Sol", "Mission", "Day", "Notes",
         "Procedure", "Section", "System", "Location", "Action", "Parts", "Test", "Signed",
         "Summary", "Sequence", "Actions", "Open", "Filed", "Classification", "Personal",
         "Log", "Maintained", "Halvard", "Reyes", "Orbital", "Ops", "Manual", "Sensor",
         "Export", "End", "From", "To", "All", "Loop", "Bed", "Quad", "Injector", "Pump",
         "Reactor", "Habitat", "Cargo", "Comms", "Nav", "Med", "Data", "Core", "Bridge",
         "Engineering", "Galley", "Translation", "Drive", "Star", "Tracker", "Reserve",
         "Battery", "Online", "Safe", "Idle", "Internal", "Release", "Fault", "Boss", "Kid",
         # common sentence starters
         "The", "This", "That", "These", "Those", "She", "Her", "Him", "His", "They",
         "Them", "Their", "You", "Your", "And", "But", "Not", "Nothing", "Then", "Also",
         "Still", "Just", "Maybe", "Everyone", "Someone", "Nobody", "Today", "Tomorrow",
         "Yesterday", "Reminder", "Note", "Anyway", "Which", "What", "When", "Where",
         "Because", "After", "Before", "Once", "Every", "Some", "None", "Sure", "Fine",
         "Good", "Right", "Okay", "Yes", "Told", "Said", "Asked", "Went", "Got", "Had"}
# Player commands each fault's teaching document must contain.
FAULT_COMMANDS = {
    "MAN-LS-03": ["run scrubber regen"], "MAN-LS-05": ["swap cartridges"],
    "MAN-LS-09": ["atmosphere flush"], "MAN-PWR-07": ["restart injector", "cycle injector"],
    "MAN-THM-04": ["isolate pump b", "recertify seal b", "restore pump b"],
    "MAN-HULL-02": ["retrieve patch kit", "patch "], "MAN-HULL-05": ["repressurize cargo"],
    "MAN-COM-01": ["align antenna"], "MAN-COM-06": ["transmit distress", "unlock core"],
    "MAN-NAV-03": ["purge nav buffer", "run star fix"], "MAN-MED-02": ["reroute habitat bus medbay"],
    "MAN-MED-04": ["revive pod"], "MAN-PROP-02": ["disable quad", "null rotation"],
}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--seed", type=int, default=1)
    args = ap.parse_args()
    out_dir = Path("corpus") / f"v{args.seed}"
    if not out_dir.exists():
        sys.exit(f"no corpus at {out_dir}")
    variant = json.loads((out_dir / "variant.json").read_text())
    plan = {d["doc_id"]: d for d in bible.build_plan(variant)}

    fired = seen = 0
    for p in sorted(out_dir.glob("*.json")):
        if p.name in ("manifest.json", "variant.json"):
            continue
        rec = json.loads(p.read_text())
        seen += 1
        body, did = rec["body"], rec["doc_id"]
        problems = []

        # Invented names: capitalized words not on the roster, not sentence-initial, not noise.
        words = re.findall(r"\b([A-Z][a-z]{2,})\b", body)
        odd = sorted({w for w in words if w not in ROSTER and w not in NOISE})
        # Report only words that appear twice AND at least once mid-sentence (not after
        # a period, newline, colon, dash or step number), and never in manuals/tables.
        def mid(w):
            return re.search(rf"(?<![.!?:\n\-—\d]\s)(?<![.!?:\n\-—])\b{w}\b(?!:)", body[1:]) is not None
        odd = [w for w in odd if len(re.findall(rf"\b{w}\b", body)) >= 2 and mid(w)]
        if odd and rec["type"] not in ("manual", "sensor", "roster"):
            problems.append(f"NAMES?    {', '.join(odd)}")

        low = body.lower()
        hits = [w for w in FORBIDDEN if w in low]
        if hits:
            problems.append(f"FORBIDDEN {', '.join(hits)}")

        if rec["md"] > 0:
            future = sorted({int(m) for m in re.findall(r"\bMD\s?(\d{1,3})\b", body) if int(m) > rec["md"]})
            # Sensor exports are dated 231 and legitimately contain 240-244 when planted.
            if future and not (rec["type"] == "sensor" and rec.get("sensitive")):
                problems.append(f"FUTURE    MD {future}")

        # After MD 214 only Okonkwo (216-218) and KES are awake; the others can't act.
        if rec["md"] >= 214:
            asleep = [n for n in ("Wren", "Wrenfield", "Sandoval", "Tanabe", "Okafor")
                      if re.search(rf"\b{n}\b", body)]
            if asleep and rec["type"] in ("log", "incident", "kes", "msg"):
                problems.append(f"STASIS?   mentions {', '.join(asleep)} after MD 214 — check they aren't awake")

        # Scrubber spares mentioned with a locker that isn't the variant's.
        if re.search(r"scrubber|cartridge", low):
            want = re.search(r"\b[A-Z]-J?\d+\b", variant["spares_location"])
            places = set(re.findall(r"(?:locker|junction|cabinet)\s+([A-Z]-J?\d+)", body))
            if want and places - {want.group()}:
                problems.append(f"VARIANT?  cartridges near {', '.join(sorted(places - {want.group()}))}; spares are {variant['spares_location']}")

        for cmd in FAULT_COMMANDS.get(did, []):
            if cmd not in low:
                problems.append(f"COMMAND   missing '{cmd}'")

        if "```" in body:
            problems.append("FENCES    markdown fence in body")

        if problems:
            fired += 1
            print(f"{did}  ({rec.get('model', '?')})")
            for pr in problems:
                print(f"    {pr}")

    missing = [d for d in plan if not (out_dir / f"{d}.json").exists()]
    if missing:
        print(f"\nNot generated yet: {', '.join(missing)}")
    print(f"\n{fired} document(s) flagged out of {seen}.")
    sys.exit(1 if fired else 0)


if __name__ == "__main__":
    main()
