#!/usr/bin/env python3
"""
server.py — the web console front end for ISV Kestrel.

  python -m kestrel.server              # then open http://127.0.0.1:8000
  python -m kestrel.server --seed 3

Everything the game does still happens in Session.handle(). This file only moves text
across HTTP and hands the browser a snapshot of the ship to draw. If you deleted it the
terminal version would still work, which was the point of keeping the interface boundary at
one function.

DEPLOYMENT NOTES (see docs/DEPLOY.md)
  Sessions live in this process's memory, so the server must run as exactly ONE instance.
  pm2 cluster mode, or a second uvicorn worker, would give each request a random process
  and every other turn would answer "no such session".

  Every setting can come from the environment, because a process manager passes environment
  variables far more reliably than it passes command-line arguments.

  A public deployment is a public API key. The turn budget below is a blunt instrument, but
  an unmetered game on the open internet is somebody else's free LLM.
"""

import argparse
import json
import os
import secrets
import time
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from . import game as game_mod

app = FastAPI(title="ISV Kestrel")
STATIC = Path(__file__).parent / "static"


def _env_int(name, default):
    try:
        return int(os.environ[name])
    except (KeyError, ValueError):
        return default


CONFIG = {
    "seed": _env_int("KESTREL_SEED", 0) or None,
    "fake_embeddings": os.environ.get("KESTREL_FAKE_EMBEDDINGS") == "1",
    # Reap idle runs. Without this a long-lived server accumulates every session anyone
    # ever started, each holding a ship, a casebook and a conversation history, until the
    # droplet runs out of memory some Tuesday.
    "session_ttl": _env_int("KESTREL_SESSION_TTL", 4 * 3600),
    "max_sessions": _env_int("KESTREL_MAX_SESSIONS", 200),
    # A crude daily ceiling on questions across all players. Set to 0 for no limit. This
    # is the only thing between a public URL and an unbounded API bill.
    "daily_turns": _env_int("KESTREL_DAILY_TURNS", 1500),
}

SESSIONS = {}          # sid -> {"session": Session, "seen": monotonic}
USAGE = {"day": None, "turns": 0}


class Say(BaseModel):
    session_id: str
    text: str


class SessionRef(BaseModel):
    session_id: str


def _reap():
    """Drop sessions nobody has touched in a while, oldest first if we are over the cap."""
    now = time.monotonic()
    ttl = CONFIG["session_ttl"]
    for sid in [k for k, v in SESSIONS.items() if now - v["seen"] > ttl]:
        SESSIONS.pop(sid, None)
    over = len(SESSIONS) - CONFIG["max_sessions"]
    if over > 0:
        for sid, _ in sorted(SESSIONS.items(), key=lambda kv: kv[1]["seen"])[:over]:
            SESSIONS.pop(sid, None)


def _session(sid):
    entry = SESSIONS.get(sid)
    if not entry:
        raise HTTPException(404, "No such session. Reload to start a new run.")
    entry["seen"] = time.monotonic()
    return entry["session"]


def _spend_turn():
    """One question against the daily ceiling. Raises when the day's budget is gone."""
    if not CONFIG["daily_turns"]:
        return
    today = time.strftime("%Y-%m-%d")
    if USAGE["day"] != today:
        USAGE.update(day=today, turns=0)
    if USAGE["turns"] >= CONFIG["daily_turns"]:
        raise HTTPException(429, "KES is out of power for today. The ship will be back "
                                 "tomorrow.")
    USAGE["turns"] += 1


def _pick_seed():
    if CONFIG["seed"] is not None:
        return CONFIG["seed"]
    import random
    indexed = sorted(int(p.name[1:]) for p in Path("index").glob("v*") if p.name[1:].isdigit())
    if not indexed:
        raise HTTPException(500, "No index found. Run: python -m kestrel.index --seed 1")
    return random.choice(indexed)


@app.post("/api/session")
def new_session():
    sid = secrets.token_urlsafe(12)
    s = game_mod.Session(seed=_pick_seed(), fake_embeddings=CONFIG["fake_embeddings"])
    SESSIONS[sid] = {"session": s, "seen": time.monotonic()}
    _reap()          # after the insert, so max_sessions is a real ceiling
    return {"session_id": sid, "seed": s.seed, "opening": s.opening(),
            "state": s.snapshot()}


@app.post("/api/say")
def say(req: Say):
    """One turn. Blocking — KES's call takes a second or two, and FastAPI runs sync
    endpoints in a threadpool, so concurrent sessions don't block each other."""
    s = _session(req.session_id)
    _spend_turn()
    reply = s.handle(req.text)
    return {"reply": reply, "state": s.snapshot()}


@app.post("/api/say_stream")
def say_stream(req: Say):
    """One turn, as newline-delimited JSON. Each line is {"delta": "..."} while KES is
    still talking, and the last is {"done": {...snapshot...}}. NDJSON rather than SSE
    because the browser side is a dozen lines of fetch + ReadableStream either way and
    this keeps the payload shape identical to the non-streaming endpoint."""
    s = _session(req.session_id)
    _spend_turn()

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


@app.get("/healthz")
def healthz():
    """For the process manager and for you at 2am. Reports whether the index actually
    loaded, which is the failure that looks like the app being fine until someone plays."""
    indexed = sorted(p.name for p in Path("index").glob("v*")) if Path("index").exists() else []
    return {"ok": bool(indexed), "corpora": indexed, "sessions": len(SESSIONS),
            "turns_today": USAGE["turns"], "daily_limit": CONFIG["daily_turns"]}


@app.get("/")
def index():
    # no-store: the page holds a session id, and a cached copy points at a dead session
    return FileResponse(STATIC / "index.html", headers={"Cache-Control": "no-store"})


app.mount("/static", StaticFiles(directory=STATIC), name="static")


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--seed", type=int, default=CONFIG["seed"],
                    help="corpus variant (default: $KESTREL_SEED, else random indexed)")
    ap.add_argument("--host", default=os.environ.get("KESTREL_HOST", "127.0.0.1"))
    ap.add_argument("--port", type=int, default=_env_int("KESTREL_PORT", 8000))
    ap.add_argument("--fake-embeddings", action="store_true")
    args = ap.parse_args()

    CONFIG["seed"] = args.seed
    CONFIG["fake_embeddings"] = args.fake_embeddings or CONFIG["fake_embeddings"]

    # Load the embedding model now rather than on the first player's first question. The
    # first call downloads and initialises it, which takes long enough to look like a hang.
    if not CONFIG["fake_embeddings"]:
        try:
            from . import rag
            seed = CONFIG["seed"] or _pick_seed()
            rag.open_collection(seed).count()
            print(f"  index v{seed} loaded")
        except Exception as e:
            print(f"  WARNING: could not open an index ({type(e).__name__}: {e})")

    import uvicorn
    print(f"\n  ISV KESTREL — http://{args.host}:{args.port}\n")
    uvicorn.run(app, host=args.host, port=args.port, log_level="warning",
                # one worker, always: sessions live in this process's memory
                workers=1)


if __name__ == "__main__":
    main()
