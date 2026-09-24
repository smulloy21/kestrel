#!/usr/bin/env python3
"""
generate_corpus.py — generate the ISV Kestrel document corpus with Claude Fable 5.1.

Usage:
  python generate_corpus.py --dry-run             # print the plan and one full prompt, no API calls
  python generate_corpus.py --limit 3             # smoke test: generate the first 3 documents
  python generate_corpus.py                       # generate everything (skips docs already on disk)
  python generate_corpus.py --only F3             # only documents tagged with fault F3
  python generate_corpus.py --only LOG-HALLORAN-0212 --force   # regenerate one document
  python generate_corpus.py --seed 7              # a different randomization variant (new corpus dir)
  python generate_corpus.py --show-options        # list the variant pools with their indices
  python generate_corpus.py --seed 2 --variant passphrase=2 --variant spares_location=1
                                                  # pick specific options (index or exact text)

Variant resolution: explicit --variant overrides > corpus/v<seed>/variant.json if it exists >
the seed. A corpus directory's variant.json is the source of truth for that corpus, so
regenerating a single document always matches its neighbors.

Output: corpus/<variant>/<doc_id>.json, one file per document, plus manifest.json and
variant.json. Each JSON file carries the document body and all metadata fields the
RAG layer needs (doc_id, type, system, md, author, access, private, sensitive, fault).
"""

import argparse
import json
import os
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

from . import bible

MODEL = os.environ.get("KESTREL_MODEL", "claude-fable-5-1")
VARIANT = None  # set in main() from --seed
DEFAULT_EFFORT = os.environ.get("KESTREL_EFFORT", "medium")   # low | medium | high
OUT_ROOT = Path("corpus")

DEFAULT_WORDS = {"manual": 450, "maint": 140, "sensor": 0, "log": 200,
                 "incident": 320, "kes": 100, "msg": 60, "roster": 0}


# ---------------------------------------------------------------------------
# Prompt assembly
# ---------------------------------------------------------------------------

def system_prompt(doc):
    author = bible.CREW.get(doc["author"])
    voice = ""
    if author:
        voice = (f"\nAUTHOR: {author['name']}, {author['role']}.\n"
                 f"VOICE: {author['voice']}\n")
    sensitive = ""
    if doc["sensitive"]:
        sensitive = ("\nSENSITIVE DOCUMENT: it records odd facts. Record them flatly and exactly as "
                     "given. No theory, no conclusion, no forbidden words. The unease should come "
                     "from the flatness.\n")
    return (f"You are writing one document for the internal archive of the starship ISV Kestrel. "
            f"It will be stored in a knowledge base and read by the ship AI, so it must be "
            f"self-contained and consistent with the world below.\n\n{bible.WORLD}\n"
            f"{bible.fixed_facts(VARIANT, doc['md'])}\n"
            f"DOCUMENT TYPE RULES:\n{bible.STYLE[doc['type']]}\n{voice}{sensitive}")


def user_prompt(doc):
    words = doc["words"] or DEFAULT_WORDS[doc["type"]]
    lines = [f"DOCUMENT ID: {doc['doc_id']}",
             f"TYPE: {doc['type']}    SYSTEM: {doc['system']}    DATE: MD {doc['md']}",
             f"BRIEF: {doc['brief']}"]
    if words:
        lines.append(f"TARGET LENGTH: about {words} words.")
    if doc["md"] >= 214:
        lines.append("\n" + bible.state_block(doc["md"]))
    if doc["type"] == "sensor" and doc["md"] >= 214:
        lines.append("\n" + bible.state_rows())
    if doc["type"] == "manual":
        lines.append("\nMANUAL INDEX (the only valid cross-reference targets; cite by ID and only "
                     "for the subject listed):")
        lines += [f"  {did}: {subj}" for did, subj in bible.manual_index() if did != doc["doc_id"]]
    if doc["include"]:
        lines.append("\nMUST INCLUDE (plant each one naturally, in passing):")
        lines += [f"  - {f}" for f in doc["include"]]
    if doc["avoid"]:
        lines.append("\nMUST NOT MENTION:")
        lines += [f"  - {f}" for f in doc["avoid"]]
    lines.append("\nWrite the document now. Body only.")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# API call
# ---------------------------------------------------------------------------

def call_claude(client, doc, effort, max_retries=5):
    import anthropic
    delay = 3
    for attempt in range(max_retries):
        try:
            # fallbacks="default" (beta): if Fable's safety classifier declines the
            # request, the API re-runs it on the model Anthropic recommends for that
            # category (an Opus model) inside the same call. The 'cyber' classifier
            # fires on benign text about passphrases, locked systems and access tiers,
            # which this corpus is full of. Declined attempts are not billed.
            resp = client.beta.messages.create(
                model=MODEL,
                max_tokens=4000,
                output_config={"effort": effort},
                system=system_prompt(doc),
                messages=[{"role": "user", "content": user_prompt(doc)}],
                fallbacks="default",
                betas=["server-side-fallback-2026-07-01"],
            )
            if resp.stop_reason == "refusal":
                cat = getattr(getattr(resp, "stop_details", None), "category", "?")
                raise RuntimeError(f"model refused ({cat}), and no fallback served it")
            # Adaptive thinking is always on: the first block may be a thinking block,
            # so pick out text blocks explicitly instead of indexing content[0].
            text = "\n".join(b.text for b in resp.content if b.type == "text").strip()
            if not text:
                raise RuntimeError("empty response")
            usage = resp.usage
            return text, usage.input_tokens, usage.output_tokens, resp.model
        except (anthropic.RateLimitError, anthropic.APIConnectionError,
                anthropic.InternalServerError) as e:
            if attempt == max_retries - 1:
                raise
            print(f"  [{doc['doc_id']}] {type(e).__name__}, retrying in {delay}s", file=sys.stderr)
            time.sleep(delay)
            delay *= 2


def strip_fences(text):
    """Belt and braces: remove a stray ```...``` wrapper if the model added one."""
    t = text.strip()
    if t.startswith("```"):
        t = t.split("\n", 1)[1] if "\n" in t else ""
        if t.rstrip().endswith("```"):
            t = t.rstrip()[:-3]
    return t.strip()


# ---------------------------------------------------------------------------
# Variant resolution
# ---------------------------------------------------------------------------

def resolve_variant(args, out_dir):
    stored_path = out_dir / "variant.json"
    stored = json.loads(stored_path.read_text()) if stored_path.exists() else None
    variant = dict(stored) if stored else bible.make_variant(args.seed)

    overrides = {}
    for item in args.variant:
        if "=" not in item:
            sys.exit(f"--variant expects KEY=VALUE, got {item!r}")
        key, value = item.split("=", 1)
        if key not in bible.VARIANT_OPTIONS:
            sys.exit(f"unknown variant key {key!r}; choose from {', '.join(bible.VARIANT_OPTIONS)}")
        pool = bible.VARIANT_OPTIONS[key]
        if key == "sticky_injector":            # the value IS the injector number, not an index
            if value.isdigit() and int(value) in pool:
                overrides[key] = int(value)
            else:
                sys.exit(f"sticky_injector must be one of {pool}")
        elif value.isdigit() and int(value) < len(pool):
            overrides[key] = pool[int(value)]
        elif value in pool:
            overrides[key] = value
        else:
            sys.exit(f"{key}={value!r} is not in the pool; run --show-options")

    if overrides and stored and any(stored.get(k) != v for k, v in overrides.items()):
        if not args.force:
            sys.exit(f"{stored_path} already exists with a different variant. Documents in that "
                     f"directory were generated with the stored variant; changing it now would make "
                     f"them inconsistent. Use a new --seed, or --force to regenerate everything.")
        print(f"WARNING: overriding the stored variant in {out_dir}; regenerate every document.")
    variant.update(overrides)
    return variant


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--dry-run", action="store_true", help="print plan and one prompt; no API calls")
    ap.add_argument("--limit", type=int, default=0, help="generate at most N documents")
    ap.add_argument("--only", default="", help="doc_id, fault tag (F3), type (log), or author (WREN)")
    ap.add_argument("--force", action="store_true", help="regenerate even if the file exists")
    ap.add_argument("--seed", type=int, default=1, help="randomization seed (variant)")
    ap.add_argument("--workers", type=int, default=3, help="parallel API calls")
    ap.add_argument("--effort", default=DEFAULT_EFFORT, choices=["low", "medium", "high"])
    ap.add_argument("--variant", action="append", default=[], metavar="KEY=VALUE",
                    help="override one variant choice; VALUE is a pool index or the exact text")
    ap.add_argument("--show-options", action="store_true", help="print the variant pools and exit")
    args = ap.parse_args()

    if args.show_options:
        for key, opts in bible.VARIANT_OPTIONS.items():
            print(key)
            for i, o in enumerate(opts):
                print(f"  [{i}] {o}")
        return

    out_dir = OUT_ROOT / f"v{args.seed}"
    variant = resolve_variant(args, out_dir)
    global VARIANT
    VARIANT = variant
    plan = bible.build_plan(variant)

    if args.only:
        key = args.only.upper()
        plan = [d for d in plan if key in (d["doc_id"], d["fault"].upper(), d["type"].upper(), d["author"].upper())]
    if not args.force:
        plan_todo = [d for d in plan if not (out_dir / f"{d['doc_id']}.json").exists()]
    else:
        plan_todo = plan
    if args.limit:
        plan_todo = plan_todo[:args.limit]

    print(f"Variant v{args.seed}: {json.dumps(variant, indent=2)}")
    print(f"Plan: {len(bible.build_plan(variant))} documents total, {len(plan)} selected, {len(plan_todo)} to generate.")

    if args.dry_run:
        for d in plan:
            flag = " " if (out_dir / f"{d['doc_id']}.json").exists() else "*"
            print(f" {flag} {d['doc_id']:<20} {d['type']:<8} MD{d['md']:<4} {d['author']:<9} "
                  f"{d['access']:<11} fault={d['fault']:<4} {'SENS' if d['sensitive'] else ''}")
        if plan:
            print("\n" + "=" * 70 + "\nSAMPLE SYSTEM PROMPT for", plan[0]["doc_id"], "\n" + "=" * 70)
            print(system_prompt(plan[0]))
            print("=" * 70 + "\nSAMPLE USER PROMPT\n" + "=" * 70)
            print(user_prompt(plan[0]))
        return

    if not os.environ.get("ANTHROPIC_API_KEY"):
        sys.exit("ANTHROPIC_API_KEY is not set. See README.md, Setup.")
    import anthropic
    client = anthropic.Anthropic()
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "variant.json").write_text(json.dumps(variant, indent=2))
    if args.variant and args.force and not args.only and not args.limit:
        plan_todo = plan  # variant changed: everything in this directory is stale

    total_in = total_out = 0
    failures = []

    def work(doc):
        text, tin, tout, served_by = call_claude(client, doc, args.effort)
        record = {k: doc[k] for k in ("doc_id", "type", "system", "md", "author",
                                      "access", "private", "sensitive", "fault")}
        record["model"] = served_by
        record["body"] = strip_fences(text)
        (out_dir / f"{doc['doc_id']}.json").write_text(json.dumps(record, indent=2, ensure_ascii=False))
        return doc["doc_id"], tin, tout, served_by

    t0 = time.time()
    with ThreadPoolExecutor(max_workers=args.workers) as ex:
        futures = {ex.submit(work, d): d for d in plan_todo}
        for i, fut in enumerate(as_completed(futures), 1):
            d = futures[fut]
            try:
                did, tin, tout, served_by = fut.result()
                total_in += tin
                total_out += tout
                note = "" if served_by.startswith(MODEL) else f"  [fallback: {served_by}]"
                print(f"[{i}/{len(plan_todo)}] {did}  ({tin} in / {tout} out){note}")
            except Exception as e:
                failures.append((d["doc_id"], str(e)))
                print(f"[{i}/{len(plan_todo)}] {d['doc_id']}  FAILED: {e}", file=sys.stderr)

    # Manifest covers every document on disk for this variant, not just this run.
    manifest = []
    for p in sorted(out_dir.glob("*.json")):
        if p.name in ("manifest.json", "variant.json"):
            continue
        rec = json.loads(p.read_text())
        manifest.append({k: rec[k] for k in rec if k != "body"} | {"chars": len(rec["body"])})
    (out_dir / "manifest.json").write_text(json.dumps(manifest, indent=2))

    # Fable 5.1 list price: $10 / M input, $50 / M output (platform.claude.com pricing page).
    # Documents served by a fallback model cost less than this estimate.
    cost = total_in / 1e6 * 10 + total_out / 1e6 * 50
    print(f"\nDone in {time.time() - t0:.0f}s. {len(plan_todo) - len(failures)} generated, "
          f"{len(failures)} failed. Tokens: {total_in} in / {total_out} out (~${cost:.2f} at list price).")
    print(f"Manifest: {out_dir / 'manifest.json'} ({len(manifest)} documents on disk)")
    if failures:
        print("Failures (re-run to retry just these):")
        for did, err in failures:
            print(f"  {did}: {err}")


if __name__ == "__main__":
    main()
