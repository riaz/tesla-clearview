"""Home-server HTTP API and web dashboard.

Trigger API (iOS Shortcut / Siri):
  POST   /depart          {"text": "leaving for work in 10 min"}  or  {"label": "work", "minutes": 10}
  GET    /sessions
  DELETE /sessions/{id}   cancel; also turns climate off if clearview turned it on

Dashboard: GET /  (sign in with CLEARVIEW_API_TOKEN, stored as an HttpOnly cookie)
Tesla login: GET /auth/login -> Tesla -> your Pages /callback/ page -> GET /auth/callback

Everything except /, /healthz and /auth/callback needs `Authorization: Bearer
$CLEARVIEW_API_TOKEN` or the dashboard cookie.
"""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import time
from datetime import datetime, timedelta
from pathlib import Path
from urllib.parse import quote

import httpx
from fastapi import Cookie, Depends, FastAPI, Header, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse
from pydantic import BaseModel

from . import auth as authmod
from . import fog as fogmod
from . import planner
from .demo import DemoFleet
from . import location
from . import session as sessmod
from . import weather
from .config import Settings, get_settings
from .fleet import FleetClient, FleetError, VehicleAsleep
from .parse import parse_departure

app = FastAPI(title="clearview")
_sessions: dict[str, tuple[sessmod.Session, asyncio.Task]] = {}
_oauth_nonces: dict[str, float] = {}  # nonce -> expiry
_last_snapshot: dict | None = None

WEB_DIR = Path(__file__).parent / "web"
_demo_fleet: DemoFleet | None = None


class _RecordingFleet:
    """Wraps the car client so every reading (dashboard or departure session) updates what
    the dashboard shows and the car's last known position."""

    def __init__(self, inner):
        self._inner = inner

    def __getattr__(self, name):
        return getattr(self._inner, name)

    async def snapshot(self):
        global _last_snapshot
        snap = await self._inner.snapshot()
        location.save_car_location(get_settings(), snap.latitude, snap.longitude)
        _last_snapshot = {**snap.__dict__, "fetched_at": datetime.now().isoformat(timespec="seconds")}
        return snap


def _fleet():
    """The car client: the simulated car when CLEARVIEW_DEMO is set, otherwise the Fleet API."""
    global _demo_fleet
    s = get_settings()
    if s.demo:
        _demo_fleet = _demo_fleet or DemoFleet(s)
        return _RecordingFleet(_demo_fleet)
    return _RecordingFleet(FleetClient(s))
COOKIE = "clearview_session"


# --- auth for this server -----------------------------------------------------

def _cookie_value(token: str) -> str:
    return hmac.new(token.encode(), b"clearview-dashboard-v1", hashlib.sha256).hexdigest()


def require_token(
    authorization: str = Header(default=""),
    clearview_session: str | None = Cookie(default=None),
) -> None:
    expected = get_settings().api_token
    if not expected:
        raise HTTPException(500, "CLEARVIEW_API_TOKEN is not set")
    if hmac.compare_digest(authorization, f"Bearer {expected}"):
        return
    if clearview_session and hmac.compare_digest(clearview_session, _cookie_value(expected)):
        return
    raise HTTPException(401, "bad token")


class LoginRequest(BaseModel):
    token: str


@app.post("/api/login")
async def dashboard_login(req: LoginRequest) -> JSONResponse:
    expected = get_settings().api_token
    if not expected:
        raise HTTPException(500, "CLEARVIEW_API_TOKEN is not set on the server")
    if not hmac.compare_digest(req.token.strip(), expected):
        await asyncio.sleep(1)  # slow down guessing
        raise HTTPException(401, "wrong token")
    resp = JSONResponse({"ok": True})
    resp.set_cookie(COOKIE, _cookie_value(expected), max_age=90 * 86400, httponly=True, samesite="lax")
    return resp


@app.post("/api/logout")
async def dashboard_logout() -> JSONResponse:
    resp = JSONResponse({"ok": True})
    resp.delete_cookie(COOKIE)
    return resp


# --- Tesla OAuth via the Pages callback bounce -----------------------------------

def _base_url(request: Request, s: Settings) -> str:
    if s.public_url:
        return s.public_url.rstrip("/")
    url = request.base_url
    # uvicorn prints http://0.0.0.0:8787, but browsers block pages sending you to 0.0.0.0.
    if url.hostname in ("0.0.0.0", "::"):
        url = url.replace(hostname="localhost")
    return str(url).rstrip("/")


@app.get("/auth/login", dependencies=[Depends(require_token)])
async def tesla_login(request: Request) -> RedirectResponse:
    s = get_settings()
    now = time.time()
    for n, exp in list(_oauth_nonces.items()):
        if exp < now:
            del _oauth_nonces[n]
    nonce, state = authmod.make_state(f"{_base_url(request, s)}/auth/callback")
    _oauth_nonces[nonce] = now + 600
    try:
        return RedirectResponse(authmod.authorize_url(s, state), status_code=303)
    except authmod.AuthError as e:
        raise HTTPException(400, str(e))


@app.get("/auth/callback")
async def tesla_callback(state: str = "", code: str = "", error: str = "", error_description: str = "") -> RedirectResponse:
    # No cookie check here: the single-use nonce proves this login was started from the
    # (authenticated) dashboard.
    nonce = authmod.state_nonce(state)
    exp = _oauth_nonces.pop(nonce, 0)
    if error:
        return RedirectResponse("/?login_error=" + quote(error_description or error), status_code=303)
    if not nonce or exp < time.time():
        return RedirectResponse("/?login_error=" + quote("Login expired or state mismatch; try again"), status_code=303)
    try:
        await asyncio.to_thread(authmod.exchange_code, get_settings(), code)
    except authmod.AuthError as e:
        return RedirectResponse("/?login_error=" + quote(str(e)), status_code=303)
    return RedirectResponse("/?login=ok", status_code=303)


@app.post("/api/tesla/logout", dependencies=[Depends(require_token)])
async def tesla_logout() -> dict:
    authmod.logout(get_settings())
    return {"ok": True}


@app.post("/api/tesla/register", dependencies=[Depends(require_token)])
async def tesla_register() -> dict:
    try:
        return await asyncio.to_thread(authmod.register_partner, get_settings())
    except authmod.AuthError as e:
        raise HTTPException(400, str(e))


# --- dashboard data ----------------------------------------------------------------

def _locations(s: Settings) -> dict:
    car = location.last_car_location(s)
    home = (s.home_lat, s.home_lon) if s.home_lat is not None and s.home_lon is not None else None
    return {
        "car": {"lat": car.lat, "lon": car.lon, "at": car.at} if car else None,
        "home": {"lat": home[0], "lon": home[1]} if home else None,
    }


@app.get("/api/overview", dependencies=[Depends(require_token)])
async def overview() -> dict:
    s = get_settings()
    return {
        "tesla": {"logged_in": True, "demo": True} if s.demo else authmod.token_info(s),
        "demo": s.demo,
        "locations": _locations(s),
        "config": {
            "client_id_set": bool(s.tesla_client_id and s.tesla_client_secret),
            "domain": s.tesla_domain,
            "region": s.tesla_region,
            "redirect_uri": s.tesla_redirect_uri,
            "pair_url": f"https://tesla.com/_ak/{s.tesla_domain}" if s.tesla_domain else None,
            "proxy": s.tesla_command_proxy_url,
            "home_set": s.home_lat is not None and s.home_lon is not None,
            "has_last_car_location": location.last_car_location(s) is not None,
            "ntfy_set": bool(s.ntfy_topic),
            "target_temp_c": s.target_temp_c,
            "sentry_on_depart": s.sentry_on_depart,
            "stop_grace_min": s.stop_grace_min,
            "default_lead_min": s.default_lead_min,
            "defrost_lead_min": s.defrost_lead_min,
        },
        "snapshot": _last_snapshot,
        "sessions": [x.to_dict() for x, _ in sorted(_sessions.values(), key=lambda v: v[0].departure, reverse=True)],
    }


@app.get("/api/setup-check", dependencies=[Depends(require_token)])
async def setup_check() -> dict:
    """Check the Pages site serves the same public key and callback page we expect."""
    s = get_settings()
    out: dict = {"public_key": None, "callback": None, "proxy": None}
    if not s.tesla_domain:
        return out
    local = Path("config/fleet-public-key.pem")
    async with httpx.AsyncClient(timeout=10, follow_redirects=True) as c:
        try:
            r = await c.get(f"https://{s.tesla_domain}/.well-known/appspecific/com.tesla.3p.public-key.pem")
            served = r.text.strip() if r.status_code == 200 else None
            out["public_key"] = {
                "status": r.status_code,
                "matches_local": bool(served and local.exists() and served == local.read_text().strip()),
                "local_found": local.exists(),
            }
        except httpx.HTTPError as e:
            out["public_key"] = {"error": str(e)}
        if s.tesla_redirect_uri.startswith("https://"):
            try:
                r = await c.get(s.tesla_redirect_uri)
                out["callback"] = {"status": r.status_code, "is_clearview": "clearview" in r.text}
            except httpx.HTTPError as e:
                out["callback"] = {"error": str(e)}
    if s.tesla_command_proxy_url:
        verify = s.tesla_proxy_ca_cert if Path(s.tesla_proxy_ca_cert).exists() else False
        try:
            async with httpx.AsyncClient(timeout=5, verify=verify) as c:
                r = await c.get(s.tesla_command_proxy_url)
            out["proxy"] = {"reachable": True, "status": r.status_code}
        except httpx.HTTPError as e:
            out["proxy"] = {"reachable": False, "error": str(e) or type(e).__name__}
    return out


def _fleet_error(e: Exception) -> HTTPException:
    if isinstance(e, authmod.AuthError):
        return HTTPException(409 if "Not logged in" in str(e) else 502, str(e))
    if isinstance(e, VehicleAsleep):
        return HTTPException(409, "Car is asleep. Use 'Wake & read' to wake it.")
    if "not been paired" in str(e):
        domain = get_settings().tesla_domain
        return HTTPException(
            409,
            f"The car doesn't have clearview's virtual key yet. On your phone, open https://tesla.com/_ak/{domain} "
            "(the Pair button in Setup), then tap your key card in the car to approve it.",
        )
    return HTTPException(502, str(e))


@app.get("/api/vehicles", dependencies=[Depends(require_token)])
async def vehicles() -> list[dict]:
    """List vehicles and their online/asleep state. Does not wake the car."""
    try:
        vs = await _fleet().list_vehicles()
    except (FleetError, authmod.AuthError) as e:
        raise _fleet_error(e)
    return [{"vin": v.get("vin"), "name": v.get("display_name"), "state": v.get("state")} for v in vs]


class SnapshotRequest(BaseModel):
    wake: bool = False


@app.post("/api/vehicle/snapshot", dependencies=[Depends(require_token)])
async def vehicle_snapshot(req: SnapshotRequest) -> dict:
    f = _fleet()
    try:
        if req.wake:
            await f.wake()
        await f.snapshot()
    except (FleetError, authmod.AuthError) as e:
        raise _fleet_error(e)
    return _last_snapshot


class AlertsRequest(BaseModel):
    wake: bool = False


@app.post("/api/vehicle/alerts", dependencies=[Depends(require_token)])
async def vehicle_alerts(req: AlertsRequest) -> list[dict]:
    """The car's recent alerts, newest first. Camera-related ones are flagged."""
    f = _fleet()
    try:
        if req.wake:
            await f.wake()
        alerts = await f.recent_alerts()
    except (FleetError, authmod.AuthError) as e:
        raise _fleet_error(e)
    out = [
        {
            "name": a.get("name"),
            "text": a.get("user_text") or a.get("name"),
            "time": a.get("time"),
            "camera": planner.is_camera_alert(a),
        }
        for a in alerts
    ]
    return sorted(out, key=lambda a: a["time"] or "", reverse=True)


@app.get("/api/conditions", dependencies=[Depends(require_token)])
async def conditions(lat: float | None = None, lon: float | None = None) -> dict:
    """Weather and fog/frost risk where you are (browser GPS), else at the car's last known
    position, else HOME_LAT/HOME_LON."""
    loc = location.resolve(get_settings(), lat, lon)
    if loc is None:
        return {"available": False, "reason": "Location unknown. Allow location in the browser, or read the car once."}
    try:
        w = await weather.current(loc.lat, loc.lon)
    except httpx.HTTPError as e:
        raise HTTPException(502, f"weather: {e}")
    cabin = _last_snapshot.get("inside_temp") if _last_snapshot else None
    f = fogmod.assess(w, cabin_temp_c=cabin)
    s = get_settings()
    lead = planner.lead_minutes(f, w.temp_c, s.default_lead_min, s.defrost_lead_min)
    return {
        "lead_min": lead,
        "available": True,
        "source": loc.source,
        "source_text": loc.describe(),
        "weather": w.__dict__,
        "fog": {
            "overall": f.overall.name,
            "exterior": f.exterior.name,
            "interior": f.interior.name,
            "frost": f.frost,
            "glass_temp_c": round(f.glass_temp_c, 1),
            "reasons": f.reasons,
        },
    }


COMMANDS = {
    "climate_on": ("climate on", lambda f, v: f.climate_on()),
    "climate_off": ("climate off", lambda f, v: f.climate_off()),
    "defrost_on": ("max defrost on", lambda f, v: f.max_defrost(True)),
    "defrost_off": ("max defrost off", lambda f, v: f.max_defrost(False)),
    "seat_heat_high": ("driver seat heat high", lambda f, v: f.seat_heater(0, 3)),
    "seat_heat_off": ("driver seat heat off", lambda f, v: f.seat_heater(0, 0)),
    "passenger_seat_heat_high": ("passenger seat heat high", lambda f, v: f.seat_heater(1, 3)),
    "passenger_seat_heat_off": ("passenger seat heat off", lambda f, v: f.seat_heater(1, 0)),
    "wheel_heat_on": ("steering wheel heat on", lambda f, v: f.steering_wheel_heater(True)),
    "wheel_heat_off": ("steering wheel heat off", lambda f, v: f.steering_wheel_heater(False)),
    "sentry_on": ("Sentry Mode on", lambda f, v: f.sentry_mode(True)),
    "sentry_off": ("Sentry Mode off", lambda f, v: f.sentry_mode(False)),
    "set_temp": ("temperature set", lambda f, v: f.set_temps(v)),
}


class CommandRequest(BaseModel):
    action: str
    value: float | None = None  # set_temp: °C


@app.post("/api/command", dependencies=[Depends(require_token)])
async def command(req: CommandRequest) -> dict:
    if req.action not in COMMANDS:
        raise HTTPException(400, f"unknown action; one of {sorted(COMMANDS)}")
    desc, fn = COMMANDS[req.action]
    if req.action == "set_temp":
        if req.value is None or not 15 <= req.value <= 28:
            raise HTTPException(400, "set_temp needs a value between 15 and 28 °C")
        desc = f"temperature set to {req.value:g}°C"
    f = _fleet()
    try:
        await f.wake()
        await fn(f, req.value)
    except (FleetError, authmod.AuthError) as e:
        raise _fleet_error(e)
    return {"ok": True, "done": desc}


# --- departure sessions (also used by Siri / Shortcuts) -----------------------------

class DepartRequest(BaseModel):
    text: str | None = None
    label: str | None = None
    minutes: float | None = None
    dry_run: bool = False
    lat: float | None = None  # your phone's location (iOS Shortcut "Get Current Location", or the dashboard)
    lon: float | None = None
    sentry: bool | None = None  # turn Sentry on while preparing; omit to use SENTRY_ON_DEPART


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
    sess = sessmod.Session(label=label, departure=at, dry_run=req.dry_run, lat=req.lat, lon=req.lon, sentry=req.sentry)
    task = asyncio.create_task(sessmod.run(sess, get_settings(), fleet=_fleet()))
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


@app.get("/", response_class=HTMLResponse)
async def dashboard() -> FileResponse:
    return FileResponse(WEB_DIR / "index.html", headers={"Cache-Control": "no-cache"})
