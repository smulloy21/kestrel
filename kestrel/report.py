#!/usr/bin/env python3
"""
report.py — read the session logs and say what happened.

  python report.py                      # summarise every session in logs/
  python report.py --last               # just the most recent one, in detail
  python report.py --html out.html      # a self-contained page: one row per turn
  python report.py --ungrounded         # only the turns where KES cited something
                                        # it was never shown

The numbers here are the ones worth putting in front of people:

  grounded      every document ID KES cited was actually in its context that turn.
                Anything below 100% is a hallucinated citation, and since the player is
                invited to click citations, it is the failure that matters most.
  retrieval     how often the documents a question needed came back at all — visible as
                distances, and as the docs the player then went on to read.
  cost          input and output tokens per turn, and what a playthrough costs.
  friction      near-misses at the parser, refused commands, failed solve attempts.
"""

import argparse
import html
import json
from collections import Counter
from pathlib import Path

LOG_ROOT = Path("logs")


def load(path):
    rows = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line:
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError:
                pass          # a half-written last line from a killed session
    return rows


def sessions(root=LOG_ROOT):
    return sorted(root.glob("session-*.jsonl"))


# ---------------------------------------------------------------------------
# Numbers
# ---------------------------------------------------------------------------

def summarise(rows):
    q = [r for r in rows if r["event"] == "question"]
    cmds = [r for r in rows if r["event"] == "command"]
    reads = [r for r in rows if r["event"] == "read"]
    solves = [r for r in rows if r["event"] == "solve"]
    misses = [r for r in rows if r["event"] == "near_miss"]
    end = next((r for r in rows if r["event"] == "session_end"), None)
    start = next((r for r in rows if r["event"] == "session_start"), {})

    cited = sum(len(r.get("cited", [])) for r in q)
    # "invented" is the failure; "elsewhere" is a real document cited without being
    # retrieved this turn, which is the archive cross-referencing itself.
    invented = sum(len(r.get("invented", r.get("ungrounded", []))) for r in q)
    elsewhere = sum(len(r.get("elsewhere", [])) for r in q)
    tin = sum(r.get("input_tokens") or 0 for r in q)
    tout = sum(r.get("output_tokens") or 0 for r in q)
    lat = [r["latency_ms"] for r in q if r.get("latency_ms")]

    return {
        "seed": start.get("seed"),
        "questions": len(q), "commands": len(cmds), "reads": len(reads),
        "solves": len(solves), "solved_ok": sum(1 for r in solves if r["ok"]),
        "near_misses": len(misses),
        "refused": sum(1 for r in cmds if not r["accepted"]),
        "citations": cited, "invented": invented, "elsewhere": elsewhere,
        "grounded_pct": (100.0 * (cited - invented) / cited) if cited else None,
        "in_tokens": tin, "out_tokens": tout,
        # Opus 5 list price, $/M tokens
        "cost_usd": tin / 1e6 * 5 + tout / 1e6 * 25,
        "median_latency_ms": sorted(lat)[len(lat) // 2] if lat else None,
        "won": end.get("won") if end else None,
        "ending": end.get("ending") if end else None,
        "turns": end.get("turns") if end else None,
        "confirmed": end.get("confirmed", []) if end else [],
        "disposition": end.get("disposition") if end else None,
        "air_left": end.get("air_left") if end else None,
        "wall_seconds": end.get("wall_seconds") if end else None,
    }


def print_summary(path, s, indent=""):
    print(f"{indent}{path.name}")
    print(f"{indent}  corpus v{s['seed']}   "
          f"{s['questions']} questions · {s['reads']} reads · {s['commands']} commands "
          f"· {s['solved_ok']}/{s['solves']} entries confirmed")
    if s["grounded_pct"] is not None:
        flag = "" if s["invented"] == 0 else f"   <-- {s['invented']} INVENTED"
        extra = f", {s['elsewhere']} cross-referenced" if s["elsewhere"] else ""
        print(f"{indent}  real citations: {s['grounded_pct']:.1f}% "
              f"({s['citations'] - s['invented']}/{s['citations']}){extra}{flag}")
    if s["in_tokens"]:
        print(f"{indent}  tokens: {s['in_tokens']} in / {s['out_tokens']} out  "
              f"(~${s['cost_usd']:.3f})   median latency "
              f"{s['median_latency_ms']} ms")
    if s["near_misses"] or s["refused"]:
        print(f"{indent}  friction: {s['near_misses']} near-miss commands, "
              f"{s['refused']} refused")
    if s["turns"] is not None:
        outcome = f"WON ({s['ending']})" if s["won"] else f"ended: {s['ending']}"
        print(f"{indent}  {outcome} · {s['air_left']}h air left · "
              f"KES {s['disposition']} · {s['wall_seconds']}s at the keyboard")
    print()


def print_detail(rows):
    """Every turn, in order. This is the view for 'why did KES say that?'"""
    for r in rows:
        e = r["event"]
        if e == "question":
            print(f"\n[{r['turn']}] Q  {r['text']}")
            if r["retrieval_query"] != r["text"]:
                print(f"     embedded: {r['retrieval_query']}")
            for h in r["hits"]:
                mark = "*" if h["doc_id"] in r.get("cited", []) else " "
                sens = " SENS" if h["sensitive"] else ""
                print(f"     {mark} {h['distance']:.3f}  {h['doc_id']}"
                      f" (chunk {h['chunk']}, {h['access']}{sens})")
            print(f"     A  {(r['answer'] or '')[:160]}")
            if r.get("invented"):
                print(f"     !! cited documents that do not exist: "
                      f"{', '.join(r['invented'])}")
        elif e == "command":
            print(f"[{r['turn']}] $  {r['text']}"
                  f"   -> {'ok' if r['accepted'] else 'refused'} ({r['hours']}h)")
        elif e == "read":
            state = "read" if r["found"] else ("sealed" if r["locked"] else "not found")
            print(f"[{r['turn']}] >  read {r['doc_id']}  -> {state}")
        elif e == "solve":
            print(f"[{r['turn']}] #  solve {r['entry_id']} "
                  f"-> {'CONFIRMED' if r['ok'] else 'wrong'} (attempt {r['attempts']})")
        elif e == "near_miss":
            print(f"      ~  {r['text']!r} -> suggested: {r['suggested']}")


def print_corpus_stats(all_rows):
    """Which documents actually get retrieved, and which never surface at all. A document
    nobody's question ever reaches is either badly written or answering a question nobody
    asks — both worth knowing before writing more of them."""
    retrieved, read, cited = Counter(), Counter(), Counter()
    for rows in all_rows:
        for r in rows:
            if r["event"] == "question":
                for h in r["hits"]:
                    retrieved[h["doc_id"]] += 1
                for c in r.get("cited", []):
                    cited[c] += 1
            elif r["event"] == "read" and r["found"]:
                read[r["doc_id"]] += 1
    print("Most retrieved documents")
    for doc, n in retrieved.most_common(12):
        print(f"  {n:4}x retrieved  {cited[doc]:3}x cited  {read[doc]:3}x opened   {doc}")
    print()


# ---------------------------------------------------------------------------
# HTML
# ---------------------------------------------------------------------------

CSS = """
:root{--hull:#0f181d;--plate:#16222a;--seam:#24343e;--frost:#bed0d8;--dim:#6b8290;
--sodium:#ffab3f;--ember:#e2563d;--lamp:#f4ecda;--go:#5fc9a0;
--mono:ui-monospace,Menlo,Consolas,monospace}
*{box-sizing:border-box}
body{background:var(--hull);color:var(--frost);font-family:var(--mono);font-size:13px;
line-height:1.55;margin:0;padding:2rem}
h1{font-size:18px;color:var(--sodium);margin:0 0 .3rem}
h2{font-size:12px;letter-spacing:.06em;color:var(--dim);font-weight:500;
margin:2rem 0 .6rem;border-bottom:1px solid var(--seam);padding-bottom:.4rem}
.sub{color:var(--dim);margin-bottom:2rem}
.cards{display:flex;flex-wrap:wrap;gap:.6rem}
.card{border:1px solid var(--seam);background:var(--plate);padding:.7rem .9rem;min-width:10rem}
.card .k{font-size:10px;letter-spacing:.05em;color:var(--dim)}
.card .v{font-size:19px;font-weight:600}
.card.bad{border-color:var(--ember)} .card.bad .v{color:var(--ember)}
.card.good .v{color:var(--go)}
.turn{border-left:2px solid var(--seam);padding:.5rem 0 .5rem .9rem;margin:.5rem 0}
.turn.q{border-left-color:var(--sodium)}
.turn.bad{border-left-color:var(--ember)}
.q-text{color:var(--lamp);font-size:14px}
.embedded{color:var(--dim);font-size:11px}
.answer{color:var(--frost);margin:.5rem 0;max-width:80ch;white-space:pre-wrap}
table{border-collapse:collapse;font-size:11.5px;margin:.4rem 0}
td{padding:.1rem .8rem .1rem 0;color:var(--dim)}
td.doc{color:var(--frost)} td.hit{color:var(--sodium)}
.warn{color:var(--ember)}
.meta{color:var(--dim);font-size:11px}
"""


def to_html(path, rows, s):
    def esc(x):
        return html.escape(str(x if x is not None else ""))

    out = [f"<!DOCTYPE html><meta charset='utf-8'><title>{esc(path.name)}</title>",
           f"<style>{CSS}</style>",
           f"<h1>ISV Kestrel — session report</h1>",
           f"<div class='sub'>{esc(path.name)} · corpus v{esc(s['seed'])}</div>",
           "<div class='cards'>"]

    def card(k, v, cls=""):
        out.append(f"<div class='card {cls}'><div class='k'>{esc(k)}</div>"
                   f"<div class='v'>{esc(v)}</div></div>")

    if s["grounded_pct"] is not None:
        card("real citations", f"{s['grounded_pct']:.0f}%",
             "good" if s["invented"] == 0 else "bad")
    card("questions", s["questions"])
    card("documents opened", s["reads"])
    card("entries confirmed", f"{s['solved_ok']}")
    if s["in_tokens"]:
        card("tokens", f"{s['in_tokens'] + s['out_tokens']:,}")
        card("cost", f"${s['cost_usd']:.3f}")
        card("median latency", f"{s['median_latency_ms']} ms")
    if s["turns"] is not None:
        card("outcome", (s["ending"] or "—"), "good" if s["won"] else "")
    out.append("</div>")

    out.append("<h2>turns</h2>")
    for r in rows:
        e = r["event"]
        if e == "question":
            bad = " bad" if r.get("invented") else ""
            out.append(f"<div class='turn q{bad}'>")
            out.append(f"<div class='q-text'>&gt; {esc(r['text'])}</div>")
            if r["retrieval_query"] != r["text"]:
                out.append(f"<div class='embedded'>embedded as: "
                           f"{esc(r['retrieval_query'])}</div>")
            out.append("<table>")
            for h in r["hits"]:
                hit = "hit" if h["doc_id"] in r.get("cited", []) else ""
                out.append(f"<tr><td>{h['distance']:.3f}</td>"
                           f"<td class='doc {hit}'>{esc(h['doc_id'])}</td>"
                           f"<td>chunk {h['chunk']}</td><td>{esc(h['type'])}</td>"
                           f"<td>{esc(h['access'])}"
                           f"{' · sensitive' if h['sensitive'] else ''}</td></tr>")
            out.append("</table>")
            out.append(f"<div class='answer'>{esc(r['answer'])}</div>")
            if r.get("invented"):
                out.append(f"<div class='warn'>cited documents that do not exist: "
                           f"{esc(', '.join(r['invented']))}</div>")
            out.append(f"<div class='meta'>{esc(r.get('model'))} · "
                       f"{esc(r.get('input_tokens'))} in / {esc(r.get('output_tokens'))} out"
                       f" · {esc(r.get('latency_ms'))} ms · air "
                       f"{esc(r.get('air_before'))}h &rarr; "
                       f"{esc(r['state']['air_hours'])}h</div></div>")
        elif e == "command":
            cls = "" if r["accepted"] else " bad"
            out.append(f"<div class='turn{cls}'><span class='q-text'>$ {esc(r['text'])}"
                       f"</span><div class='meta'>"
                       f"{'accepted' if r['accepted'] else 'refused'} · {r['hours']}h · "
                       f"{esc(r['response'][:120])}</div></div>")
        elif e == "read":
            state = "read" if r["found"] else ("sealed" if r["locked"] else "not in archive")
            out.append(f"<div class='turn'><span class='meta'>opened "
                       f"<b>{esc(r['doc_id'])}</b> — {state}</span></div>")
        elif e == "solve":
            out.append(f"<div class='turn'><span class='meta'>reconstruction "
                       f"<b>{esc(r['entry_id'])}</b> — "
                       f"{'confirmed' if r['ok'] else 'wrong, attempt ' + str(r['attempts'])}"
                       f"</span></div>")
        elif e == "near_miss":
            out.append(f"<div class='turn bad'><span class='meta'>parser miss: "
                       f"{esc(r['text'])} &rarr; {esc(r['suggested'])}</span></div>")
    return "\n".join(out)


# ---------------------------------------------------------------------------

def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--dir", default="logs")
    ap.add_argument("--last", action="store_true", help="detail for the most recent session")
    ap.add_argument("--html", help="write a session report to this path")
    ap.add_argument("--ungrounded", action="store_true",
                    help="show only turns where KES cited something it was not shown")
    ap.add_argument("--corpus", action="store_true", help="document retrieval statistics")
    args = ap.parse_args()

    paths = sessions(Path(args.dir))
    if not paths:
        raise SystemExit(f"no session logs in {args.dir}/")

    if args.ungrounded:
        found = 0
        for p in paths:
            for r in load(p):
                if r.get("invented"):
                    found += 1
                    print(f"{p.name} turn {r['turn']}: {r['text']}")
                    print(f"   cited {', '.join(r['invented'])}, "
                          f"retrieved {', '.join(h['doc_id'] for h in r['hits'])}")
                    print(f"   {r['answer'][:200]}\n")
        print(f"{found} turn(s) with an invented citation across {len(paths)} session(s).")
        return

    if args.corpus:
        print_corpus_stats([load(p) for p in paths])
        return

    if args.last or args.html:
        p = paths[-1]
        rows = load(p)
        s = summarise(rows)
        if args.html:
            Path(args.html).write_text(to_html(p, rows, s), encoding="utf-8")
            print(f"wrote {args.html}")
        else:
            print_summary(p, s)
            print_detail(rows)
        return

    totals = Counter()
    for p in paths:
        s = summarise(load(p))
        print_summary(p, s)
        totals["citations"] += s["citations"]
        totals["invented"] += s["invented"]
        totals["in"] += s["in_tokens"]
        totals["out"] += s["out_tokens"]
    if totals["citations"]:
        pct = 100.0 * (totals["citations"] - totals["invented"]) / totals["citations"]
        print(f"ACROSS {len(paths)} SESSIONS: {pct:.1f}% real citations "
              f"({totals['citations'] - totals['invented']}/{totals['citations']}), "
              f"{totals['in'] + totals['out']:,} tokens, "
              f"~${totals['in'] / 1e6 * 5 + totals['out'] / 1e6 * 25:.2f}")


if __name__ == "__main__":
    main()
