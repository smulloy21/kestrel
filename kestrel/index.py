#!/usr/bin/env python3
"""
build_index.py — chunk a corpus and load it into Chroma.

  python build_index.py --seed 1          # index corpus/v1 into index/v1 (replaces any old index)
  python build_index.py --seed 1 --stats  # also print chunk counts by document type

Run this once per corpus, and again whenever you regenerate documents. It takes about a
minute for 98 documents; the first run also downloads the embedding model (~80 MB).
"""
import argparse
from collections import Counter

from . import rag


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--seed", type=int, default=1)
    ap.add_argument("--stats", action="store_true")
    ap.add_argument("--fake-embeddings", action="store_true",
                    help="TEST ONLY: hash-based embeddings, no model download, no real semantics")
    args = ap.parse_args()

    col = rag.index_corpus(args.seed, fake=args.fake_embeddings)

    if args.stats:
        res = col.get(include=["metadatas"])
        by_type = Counter(m["type"] for m in res["metadatas"])
        by_access = Counter(m["access"] for m in res["metadatas"])
        print("chunks by type:  ", dict(by_type))
        print("chunks by access:", dict(by_access))
        print("private:sam:     ", sum(1 for m in res["metadatas"] if m["private"] == "sam"))
        print("sensitive:       ", sum(1 for m in res["metadatas"] if m["sensitive"]))


if __name__ == "__main__":
    main()
