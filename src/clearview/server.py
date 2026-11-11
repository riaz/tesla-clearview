"""Small HTTP trigger API so an iOS Shortcut, Siri or a widget can start a departure.

POST /depart   {"text": "leaving for work in 10 min"}  or  {"label": "work", "minutes": 10}
GET  /sessions
DELETE /sessions/{id}    cancel; also turns climate off if clearview turned it on

All requests need `Authorization: Bearer $CLEARVIEW_API_TOKEN`.
"""

from __future__ import annotations

import asyncio
import hmac
from datetime import datetime, timedelta

from fastapi import Depends, FastAPI, Header, HTTPException
from pydantic import BaseModel

from . import session as sessmod
from .config import get_settings
from .parse import parse_departure

app = FastAPI(title="clearview")
_sessions: dict[str, tuple[sessmod.Session, asyncio.Task]] = {}


def require_token(authorization: str = Header(default="")) -> None:
    expected = get_settings().api_token
    if not expected:
        raise HTTPException(500, "CLEARVIEW_API_TOKEN is not set")
    if not hmac.compare_digest(authorization, f"Bearer {expected}"):
        raise HTTPException(401, "bad token")


class DepartRequest(BaseModel):
    text: str | None = None
    label: str | None = None
    minutes: float | None = None
    dry_run: bool = False


@app.post("/depart", dependencies=[Depends(require_token)])
async def depart(req: DepartRequest) -> dict:
    if req.text:
        try:
            d = parse_departure(req.text)
        except ValueError as e:
            raise HTTPException(400, str(e))
        label, at = (req.label or d.label), d.at
    elif req.minutes is not None:
        label, at = req.label or "drive", datetime.now() + timedelta(minutes=req.minutes)
    else:
        raise HTTPException(400, "give `text` or `minutes`")
    if at - datetime.now() > timedelta(hours=12):
        raise HTTPException(400, "departure more than 12 h away")

    # Only one active session; a new request replaces the old one.
    for sid, (s, t) in list(_sessions.items()):
        if not t.done():
            t.cancel()
    sess = sessmod.Session(label=label, departure=at, dry_run=req.dry_run)
    task = asyncio.create_task(sessmod.run(sess, get_settings()))
    _sessions[sess.id] = (sess, task)
    await asyncio.sleep(0)
    return sess.to_dict()


@app.get("/sessions", dependencies=[Depends(require_token)])
async def list_sessions() -> list[dict]:
    return [s.to_dict() for s, _ in _sessions.values()]


@app.delete("/sessions/{sid}", dependencies=[Depends(require_token)])
async def cancel(sid: str) -> dict:
    if sid not in _sessions:
        raise HTTPException(404, "no such session")
    s, t = _sessions[sid]
    if not t.done():
        t.cancel()
        try:
            await t
        except asyncio.CancelledError:
            pass
    return s.to_dict()


@app.get("/healthz")
async def healthz() -> dict:
    return {"ok": True}
