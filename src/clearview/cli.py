"""clearview command line."""

from __future__ import annotations

import asyncio
import json
import logging
import tempfile
from datetime import datetime, timedelta
from pathlib import Path

import typer

from . import auth as authmod
from . import cameras, planner, session as sessmod, vision
from .config import get_settings
from .fleet import FleetClient
from .notify import Notifier
from .parse import parse_departure

app = typer.Typer(help="Tesla windscreen/camera clarity + smart pre-departure climate.", no_args_is_help=True)
auth_app = typer.Typer(help="Tesla Fleet API authentication.", no_args_is_help=True)
app.add_typer(auth_app, name="auth")


@app.callback()
def _main(verbose: bool = typer.Option(False, "-v", "--verbose")) -> None:
    logging.basicConfig(level=logging.INFO if verbose else logging.WARNING, format="%(asctime)s %(name)s %(message)s")


@auth_app.command("login")
def auth_login(no_browser: bool = typer.Option(False, help="Print the URL instead of opening a browser")) -> None:
    """Log in with your Tesla account (stores tokens in ~/.config/clearview)."""
    authmod.login(get_settings(), open_browser=not no_browser)


@auth_app.command("register")
def auth_register() -> None:
    """One-time partner registration of TESLA_DOMAIN with your Fleet API region."""
    print(json.dumps(authmod.register_partner(get_settings()), indent=2))


@auth_app.command("pair-url")
def pair_url() -> None:
    """Print the link that installs clearview's virtual key on your car (open it on your phone)."""
    print(f"https://tesla.com/_ak/{get_settings().tesla_domain}")


@app.command()
def vehicles() -> None:
    """List vehicles on the account (does not wake them)."""
    for v in asyncio.run(FleetClient(get_settings()).list_vehicles()):
        print(f"{v['vin']}  {v.get('display_name') or ''}  state={v.get('state')}")


@app.command()
def status(wake: bool = typer.Option(False, help="Wake the car first (uses battery and API credit)")) -> None:
    """Show climate/battery state."""
    async def go():
        f = FleetClient(get_settings())
        if wake:
            await f.wake()
        return await f.snapshot()
    print(json.dumps(asyncio.run(go()).__dict__, indent=2, default=str))


def _run_session(label: str, at: datetime, dry_run: bool, lat: float | None = None, lon: float | None = None) -> None:
    s = sessmod.Session(label=label, departure=at, dry_run=dry_run, lat=lat, lon=lon)
    print(f"Departure '{label}' at {at:%H:%M}. Ctrl-C cancels (and turns climate off if clearview started it).")
    try:
        asyncio.run(sessmod.run(s, get_settings()))
    except KeyboardInterrupt:
        pass
    print("\n".join(s.events))
    print(f"status: {s.status}  plan: {s.plan}")


@app.command()
def depart(
    label: str = typer.Argument("drive", help="Where you're going, e.g. 'work'"),
    minutes: float = typer.Option(10, "--in", help="Minutes until you leave"),
    dry_run: bool = typer.Option(False, help="Check everything but send no commands to the car"),
    lat: float = typer.Option(None, help="Where you are now (defaults to the car's last known position)"),
    lon: float = typer.Option(None),
) -> None:
    """Prepare the car for a departure N minutes from now."""
    _run_session(label, datetime.now() + timedelta(minutes=minutes), dry_run, lat, lon)


@app.command()
def say(
    text: str,
    dry_run: bool = typer.Option(False),
    lat: float = typer.Option(None, help="Where you are now (defaults to the car's last known position)"),
    lon: float = typer.Option(None),
) -> None:
    """Natural phrasing: clearview say "leaving for work in 10 min"."""
    d = parse_departure(text)
    _run_session(d.label, d.at, dry_run, lat, lon)


@app.command()
def analyze(
    path: Path = typer.Argument(..., help="TeslaCam folder or a folder of <camera>.jpg stills"),
    max_age_min: int = typer.Option(0, help="Only use clips newer than this (0 = any age)"),
    notify: bool = typer.Option(False, help="Also push the result to ntfy"),
) -> None:
    """Run the camera fog/obstruction check on its own (no car connection needed)."""
    s = get_settings()
    frames = cameras.frames_from_path(
        path.expanduser(), Path(tempfile.mkdtemp(prefix="clearview-")),
        max_age=timedelta(minutes=max_age_min) if max_age_min else None,
    )
    if not frames:
        raise typer.Exit("No clips/images found.")
    report = vision.analyze(frames, s.anthropic_model)
    print(report.model_dump_json(indent=2))
    alerts = planner.camera_alerts(report)
    if notify:
        n = Notifier(s)
        asyncio.run(n.send(
            "🧽 Clean camera(s)" if alerts else "✅ Cameras clear",
            "\n".join(alerts) or report.summary,
            priority="high" if alerts else "default",
        ))


@app.command()
def serve() -> None:
    """Run the web dashboard + HTTP trigger API (for the browser, iOS Shortcuts / Siri)."""
    import uvicorn

    s = get_settings()
    if not s.api_token:
        raise typer.Exit("Set CLEARVIEW_API_TOKEN in .env first (openssl rand -hex 24).")
    shown = s.public_url or f"http://{'localhost' if s.api_host in ('0.0.0.0', '127.0.0.1') else s.api_host}:{s.api_port}"
    print(f"Dashboard: {shown}  (sign in with CLEARVIEW_API_TOKEN)")
    uvicorn.run("clearview.server:app", host=s.api_host, port=s.api_port)


if __name__ == "__main__":
    app()
