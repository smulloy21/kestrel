#!/usr/bin/env python3
"""
server.py — the web console front end for ISV Kestrel.

  pip install fastapi uvicorn
  python server.py                 # then open http://127.0.0.1:8000
  python server.py --seed 3

Everything the game does still happens in Session.handle(). This file only moves text
across HTTP and hands the browser a snapshot of the ship to draw. If you deleted it the
terminal version would still work, which was the point of keeping the interface boundary at
one function.

Sessions live in memory, keyed by an id the browser holds. Restarting the server ends any
run in progress; for a single-player local game that is the right trade.
"""

import argparse
import json
import secrets
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from . import game as game_mod

app = FastAPI(title="ISV Kestrel")
SESSIONS = {}
CONFIG = {"seed": None, "fake_embeddings": False}
STATIC = Path(__file__).parent / "static"


class Say(BaseModel):
    session_id: str
    text: str


class SessionRef(BaseModel):
    session_id: str


def _session(sid):
    s = SESSIONS.get(sid)
    if not s:
        raise HTTPException(404, "No such session. Reload to start a new run.")
    return s


def _pick_seed():
    if CONFIG["seed"] is not None:
        return CONFIG["seed"]
    import random
    indexed = sorted(int(p.name[1:]) for p in Path("index").glob("v*") if p.name[1:].isdigit())
    if not indexed:
        raise HTTPException(500, "No index found. Run: python build_index.py --seed 1")
    return random.choice(indexed)


@app.post("/api/session")
def new_session():
    sid = secrets.token_urlsafe(12)
    s = game_mod.Session(seed=_pick_seed(), fake_embeddings=CONFIG["fake_embeddings"])
    SESSIONS[sid] = s
    return {"session_id": sid, "seed": s.seed, "opening": s.opening(),
            "state": s.snapshot()}


@app.post("/api/say")
def say(req: Say):
    """One turn. Blocking — KES's call takes a second or two, and FastAPI runs sync
    endpoints in a threadpool, so concurrent sessions don't block each other."""
    s = _session(req.session_id)
    reply = s.handle(req.text)
    return {"reply": reply, "state": s.snapshot()}


@app.post("/api/say_stream")
def say_stream(req: Say):
    """One turn, as newline-delimited JSON. Each line is {"delta": "..."} while KES is
    still talking, and the last is {"done": {...snapshot...}}. NDJSON rather than SSE
    because the browser side is a dozen lines of fetch + ReadableStream either way and
    this keeps the payload shape identical to the non-streaming endpoint."""
    s = _session(req.session_id)

    def gen():
        try:
            for event in s.handle_stream(req.text):
                yield json.dumps(event) + "\n"
        except Exception as e:  # never leave the browser hanging on a half-written turn
            yield json.dumps({"delta": f"\n[console link interrupted: {type(e).__name__}]",
                              "done": s.snapshot()}) + "\n"

    return StreamingResponse(gen(), media_type="application/x-ndjson")


@app.get("/api/state/{sid}")
def state(sid: str):
    return _session(sid).snapshot()


@app.get("/api/doc/{sid}/{doc_id}")
def document(sid: str, doc_id: str):
    return _session(sid).document(doc_id)


class Solve(BaseModel):
    session_id: str
    entry_id: str
    answers: list[str]


@app.post("/api/solve")
def solve(req: Solve):
    """Check one reconstruction entry. Deterministic — no model involved."""
    s = _session(req.session_id)
    ok, msg, _ = s.casebook.solve(req.entry_id, req.answers, s.ship.tier)
    if ok:
        s._apply_grants(req.entry_id.upper())
    s.ship.full_record_available = s.casebook.can_send_full_record()
    return {"ok": ok, "message": msg, "state": s.snapshot()}


@app.get("/api/commands")
def commands():
    from . import ship
    return {"text": ship.COMMAND_REFERENCE}


@app.get("/")
def index():
    return FileResponse(STATIC / "index.html")


app.mount("/static", StaticFiles(directory=STATIC), name="static")


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--seed", type=int, default=None, help="corpus variant (default: random indexed)")
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=8000)
    ap.add_argument("--fake-embeddings", action="store_true")
    args = ap.parse_args()

    CONFIG["seed"] = args.seed
    CONFIG["fake_embeddings"] = args.fake_embeddings

    import uvicorn
    print(f"\n  ISV KESTREL — open http://{args.host}:{args.port}\n")
    uvicorn.run(app, host=args.host, port=args.port, log_level="warning")


if __name__ == "__main__":
    main()
