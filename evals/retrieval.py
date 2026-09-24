#!/usr/bin/env python3
"""
eval_retrieval.py — does the index return the right documents for the questions that matter?

  python eval_retrieval.py --seed 1
  python eval_retrieval.py --seed 1 -k 8 -v      # more results per question, show what came back

golden_questions.json lists questions a player will ask and the documents that MUST be
among the results. For each, we retrieve k chunks and check which expected documents appear
(hit@k). It's a five-second regression test: run it after regenerating documents, after
changing the chunking, and after swapping the embedding model. A question whose expected
documents never show up means either the corpus doesn't say it clearly enough or the
chunking split it badly — both are fixable before KES ever sees a prompt.
"""
import argparse
import json
from pathlib import Path

from kestrel import rag


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--seed", type=int, default=1)
    ap.add_argument("-k", type=int, default=8)
    ap.add_argument("-v", action="store_true", help="show the retrieved documents per question")
    ap.add_argument("--fake-embeddings", action="store_true", help="TEST ONLY; must match the index")
    ap.add_argument("--no-keyword", action="store_true",
                    help="vector search only, to isolate what hybrid retrieval is doing")
    ap.add_argument("--sweep", action="store_true",
                    help="try several values of k and report how coverage changes")
    ap.add_argument("--no-expand", action="store_true",
                    help="skip the adjacent-chunk expansion of the top hit")
    args = ap.parse_args()

    col = rag.open_collection(args.seed, fake=args.fake_embeddings)
    golden = json.load(open(Path(__file__).parent / "golden_questions.json"))

    if args.sweep:
        # How many documents does KES actually need in front of it? Asking for more costs
        # tokens and dilutes attention, so the answer should be measured rather than
        # guessed at. `any` is the number that matters most in play: if even one of the
        # right documents is there, KES can cite it and the player can follow it.
        print(f"{'k':>4}  {'all expected':>13}  {'at least one':>13}")
        for k in (4, 6, 8, 10, 12, 16):
            found = total = anyhit = 0
            for g in golden:
                hits = rag.retrieve(col, g["q"], k=k, tier=g.get("tier", "crew"),
                                    identified=g.get("identified", False),
                                    keyword=not args.no_keyword,
                                    expand_best=not args.no_expand)
                got = {h["meta"]["doc_id"] for h in hits}
                hit = [d for d in g["expect"] if d in got]
                found += len(hit)
                total += len(g["expect"])
                anyhit += 1 if hit else 0
            print(f"{k:>4}  {found}/{total} ({100*found/total:>3.0f}%)  "
                  f"{anyhit}/{len(golden)} ({100*anyhit/len(golden):>3.0f}%)")
        return
    total_expected = total_found = 0
    distinct, weak = [], []
    any_hit = 0
    for g in golden:
        hits = rag.retrieve(col, g["q"], k=args.k, tier=g.get("tier", "crew"),
                            identified=g.get("identified", False),
                            keyword=not args.no_keyword,
                            expand_best=not args.no_expand)
        got = []
        for h in hits:
            if h["meta"]["doc_id"] not in got:
                got.append(h["meta"]["doc_id"])
        found = [d for d in g["expect"] if d in got]
        missing = [d for d in g["expect"] if d not in got]
        total_expected += len(g["expect"])
        total_found += len(found)
        distinct.append(len(got))
        if found:
            any_hit += 1
        # A question whose best hit is far away did not really match anything; that is a
        # different problem from matching the wrong thing, and it needs a different fix.
        if hits and hits[0]["distance"] > 0.6:
            weak.append((g["q"], hits[0]["distance"]))
        mark = "OK  " if not missing else "MISS"
        print(f"{mark} {len(found)}/{len(g['expect'])}  {g['q']!r}"
              + (f"  missing: {', '.join(missing)}" if missing else ""))
        if args.v:
            print(f"       {len(got)} distinct docs, best {hits[0]['distance']:.3f}: "
                  + ", ".join(got))
    print(f"\nhit@{args.k}: {total_found}/{total_expected} expected documents retrieved "
          f"({100 * total_found / total_expected:.0f}%). Aim for 85%+.")
    print(f"at least one expected document: {any_hit}/{len(golden)} questions "
          f"({100 * any_hit / len(golden):.0f}%) — the one that decides whether KES can "
          f"answer at all")
    avg = sum(distinct) / len(distinct) if distinct else 0
    print(f"distinct documents per question: {avg:.1f} of {args.k} slots"
          + ("   <-- chunks of the same document are crowding the results"
             if avg < args.k - 0.5 else ""))
    if weak:
        print(f"\n{len(weak)} question(s) matched nothing closely (best hit > 0.6). The "
              f"corpus may not say this in words a player would use:")
        for q, d in weak:
            print(f"  {d:.3f}  {q!r}")


if __name__ == "__main__":
    main()
