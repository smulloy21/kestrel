#!/usr/bin/env python3
"""
query.py — ask the index a question and see what comes back. No LLM involved.

  python query.py "why is coolant pressure dropping"
  python query.py "who am I" --tier engineering --identified
  python query.py --doc MAINT-0203                # print a whole document, as `read` would
  python query.py "..." --seed 2 -k 10

This is the tool for judging the corpus and the chunking. If the right documents don't
show up here, KES can't cite them, no matter how good its prompt is. Distances are
cosine: lower is closer. With the default model, hits under ~0.5 are strong, 0.5–0.7 are
plausible, above 0.7 are usually noise.
"""
import argparse
import textwrap

from . import rag


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("question", nargs="?", default="")
    ap.add_argument("--seed", type=int, default=1)
    ap.add_argument("-k", type=int, default=8)
    ap.add_argument("--tier", default="crew", choices=rag.TIER_ORDER)
    ap.add_argument("--identified", action="store_true", help="player has passed the Sam check")
    ap.add_argument("--doc", help="print one full document instead of searching")
    ap.add_argument("--full", action="store_true", help="print whole chunks, not snippets")
    ap.add_argument("--fake-embeddings", action="store_true", help="TEST ONLY; must match how the index was built")
    args = ap.parse_args()

    col = rag.open_collection(args.seed, fake=args.fake_embeddings)

    if args.doc:
        doc = rag.get_document(col, args.doc.upper(), tier=args.tier, identified=args.identified)
        print(doc["body"] if doc else f"No such document (or not accessible at tier {args.tier}).")
        return
    if not args.question:
        ap.error("give a question or --doc")

    hits = rag.retrieve(col, args.question, k=args.k, tier=args.tier, identified=args.identified)
    print(f"tier={args.tier} identified={args.identified}  →  {len(hits)} hits\n")
    for i, h in enumerate(hits, 1):
        m = h["meta"]
        flags = ("SENS " if m["sensitive"] else "") + (f"{m['access']} " if m["access"] != "crew" else "")
        print(f"{i}. {h['distance']:.3f}  {m['doc_id']}  ({m['type']}, MD {m['md']}, chunk "
              f"{m['chunk_index'] + 1}/{m['chunk_count']}) {flags}")
        body = h["text"].split("\n", 1)[1] if "\n" in h["text"] else h["text"]
        if args.full:
            print(textwrap.indent(body, "     "))
        else:
            print(textwrap.indent(textwrap.shorten(body, 240, placeholder=" …"), "     "))
        print()


if __name__ == "__main__":
    main()
