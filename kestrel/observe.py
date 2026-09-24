"""
observe.py — what happened, and why.

THE PROBLEM THIS SOLVES
When a playtester says "KES gave me a weird answer about the coolant loop", there is
nothing to look at. The answer is gone, the six chunks it was written from are gone, and
the ship state that framed it is gone. You cannot debug a RAG system from a screenshot of
its output, because the output is the last step of five and almost every failure happens
earlier: the wrong chunks were retrieved, or the right chunks were retrieved and the model
ignored them, or an access filter hid the document that mattered.

So: one line of JSON per turn, one file per playthrough, written as the game runs.

WHY NOT A TRACING PLATFORM
Langfuse, LangSmith and Phoenix are good tools built for tracing chains you did not write.
This pipeline is five functions in two files, all of them ours. A dependency, an account
and a network hop would buy indirection rather than insight. A JSONL file is greppable,
works offline, survives a failed deploy, and is already the shape the eval harness wants.

THE MEASUREMENT THAT MATTERS
`grounded`: every document ID KES cited, checked against the documents actually retrieved
for that turn. If KES cites MAINT-0203 and MAINT-0203 was not in its context, the model
invented a citation — the exact failure mode this game cannot tolerate, since the player
is invited to click it. It costs nothing to compute and needs no judge model.
"""

import json
import os
import re
import time
from datetime import datetime, timezone
from pathlib import Path

LOG_ROOT = Path(os.environ.get("KESTREL_LOG_DIR", "logs"))
DOC_ID = re.compile(r"\b(?:MAN|MAINT|SENS|LOG|INC|KES|MSG|DATA)-[A-Z0-9][A-Z0-9-]*\b")


def cited_ids(text):
    """Document IDs KES named in an answer. Trailing punctuation is stripped because
    '(MAINT-0203).' is how a citation usually appears in prose."""
    return sorted({m.rstrip(".,;:)") for m in DOC_ID.findall(text or "")})


class SessionLog:
    """One playthrough. Append-only; each turn is a self-contained JSON object, so a
    crashed session still leaves a readable file."""

    def __init__(self, seed, variant=None, enabled=True, log_dir=None):
        self.enabled = enabled
        self.turn = 0
        self.started = time.time()
        self.path = None
        if not enabled:
            return
        root = Path(log_dir) if log_dir else LOG_ROOT
        root.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
        self.path = root / f"session-{stamp}-v{seed}.jsonl"
        self._write({"event": "session_start", "seed": seed, "variant": variant or {},
                     "ts": self._now()})

    # ------------------------------------------------------------------

    def _now(self):
        return datetime.now(timezone.utc).isoformat(timespec="seconds")

    def _write(self, row):
        if not self.enabled or not self.path:
            return
        with self.path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")

    def _state(self, ship, casebook):
        """The game context a turn happened in. Without this, a log line is an answer with
        no idea what the ship looked like when it was given."""
        return {
            "air_hours": round(ship.air_hours, 2),
            "hours_elapsed": round(ship.hours_elapsed, 2),
            "tier": ship.tier,
            "identified": ship.identified,
            "alerts": len(ship.alerts()),
            "confirmed": len(casebook.confirmed),
            "disposition": casebook.disposition()["name"],
        }

    # ------------------------------------------------------------------
    # Turn types
    # ------------------------------------------------------------------

    def question(self, *, text, retrieval_query, hits, answer, trace, ship, casebook,
                 air_before, known=None):
        """A question put to KES: the whole RAG path in one row.

        `known` is every document ID in the archive. Without it there is only "cited but
        not retrieved", which conflates two opposite things: an ID the model fabricated,
        and a real cross-reference the archive prints inside a document KES *was* shown
        (MAN-COM-06 genuinely says "per MAN-DATA-02"). Only the first is a hallucination.
        """
        self.turn += 1
        retrieved = [h["meta"]["doc_id"] for h in hits]
        cites = cited_ids(answer)
        row = {
            "event": "question", "turn": self.turn, "ts": self._now(),
            "text": text,
            # the embedded query differs from the question when a short follow-up is
            # expanded with the previous one, and that difference explains a lot of
            # surprising retrievals
            "retrieval_query": retrieval_query,
            "k": len(hits),
            "hits": [{"doc_id": h["meta"]["doc_id"],
                      "chunk": h["meta"]["chunk_index"],
                      "distance": round(h["distance"], 4),
                      "type": h["meta"]["type"],
                      "access": h["meta"]["access"],
                      "sensitive": bool(h["meta"]["sensitive"])}
                     for h in hits],
            "answer": answer,
            "answer_chars": len(answer or ""),
            "cited": cites,
            # the headline metric: did KES cite anything that does not exist?
            "invented": [c for c in cites if known is not None and c not in known],
            # real, but not in this turn's context — a cross-reference, or KES naming
            # something it would need to pull. Worth seeing, not a failure.
            "elsewhere": [c for c in cites if c not in retrieved
                          and (known is None or c in known)],
            "grounded": all(c in retrieved for c in cites),
            "model": trace.get("model"),
            "input_tokens": trace.get("input_tokens"),
            "output_tokens": trace.get("output_tokens"),
            "latency_ms": trace.get("latency_ms"),
            "stop_reason": trace.get("stop_reason"),
            "fallback": bool(trace.get("model") and trace.get("requested_model")
                             and not str(trace["model"]).startswith(
                                 str(trace["requested_model"]))),
            "air_before": round(air_before, 2),
            "state": self._state(ship, casebook),
        }
        self._write(row)

    def command(self, *, text, result, ship, casebook, air_before):
        self.turn += 1
        self._write({
            "event": "command", "turn": self.turn, "ts": self._now(),
            "text": text,
            "accepted": bool(result.ok),
            "hours": result.hours,
            "fatal": bool(result.fatal),
            "response": result.text,
            "air_before": round(air_before, 2),
            "state": self._state(ship, casebook),
        })

    def read(self, *, doc_id, found, locked, ship, casebook):
        self.turn += 1
        self._write({"event": "read", "turn": self.turn, "ts": self._now(),
                     "doc_id": doc_id, "found": found, "locked": locked,
                     "state": self._state(ship, casebook)})

    def solve(self, *, entry_id, answers, ok, attempts, ship, casebook):
        self.turn += 1
        self._write({"event": "solve", "turn": self.turn, "ts": self._now(),
                     "entry_id": entry_id, "answers": answers, "ok": ok,
                     "attempts": attempts, "state": self._state(ship, casebook)})

    def near_miss(self, *, text, syntax):
        """A line that looked like a command but did not parse. A pile of these means the
        parser or the manuals are failing the player."""
        self._write({"event": "near_miss", "ts": self._now(),
                     "text": text, "suggested": syntax})

    def end(self, *, ship, casebook):
        self._write({"event": "session_end", "ts": self._now(),
                     "won": ship.won, "ending": ship.ending,
                     "turns": self.turn,
                     "wall_seconds": round(time.time() - self.started, 1),
                     "air_left": round(ship.air_hours, 2),
                     "hours_elapsed": round(ship.hours_elapsed, 2),
                     "confirmed": sorted(casebook.confirmed),
                     "disposition": casebook.disposition()["name"]})
