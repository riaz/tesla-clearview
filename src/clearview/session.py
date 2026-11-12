"""One departure run: plan the lead time, wake the car once, check the glass and
cameras, set climate, then shut it off if you don't leave.

Nothing runs in the background between sessions. The car only hears from
clearview after you say you're leaving.
"""

from __future__ import annotations

import asyncio
import logging
import tempfile
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path

from . import cameras, fog as fogmod, location, planner, vision, weather
from .config import Settings
from .fleet import FleetClient, FleetError, VehicleAsleep
from .notify import Notifier

log = logging.getLogger(__name__)


@dataclass
class Session:
    label: str
    departure: datetime
    dry_run: bool = False
    lat: float | None = None  # where you are when asking (phone GPS); optional
    lon: float | None = None
    sentry: bool | None = None  # turn Sentry on while preparing; None = SENTRY_ON_DEPART
    created_at: datetime = field(default_factory=datetime.now)
    woke_at: datetime | None = None
    checked_at: datetime | None = None
    glass: str | None = None  # short summary of the glass check, e.g. "Frost"
    acted_at: datetime | None = None
    id: str = field(default_factory=lambda: uuid.uuid4().hex[:8])
    status: str = "scheduled"
    start_at: datetime | None = None
    plan: str | None = None
    events: list[str] = field(default_factory=list)

    def note(self, msg: str) -> None:
        line = f"{datetime.now():%H:%M:%S} {msg}"
        self.events.append(line)
        log.info("[%s] %s", self.id, msg)

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "label": self.label,
            "departure": self.departure.isoformat(timespec="minutes"),
            "start_at": self.start_at.isoformat(timespec="minutes") if self.start_at else None,
            "status": self.status,
            "plan": self.plan,
            "dry_run": self.dry_run,
            "events": self.events,
            "created_at": _iso(self.created_at),
            "woke_at": _iso(self.woke_at),
            "checked_at": _iso(self.checked_at),
            "glass": self.glass,
            "acted_at": _iso(self.acted_at),
        }


def _iso(t: datetime | None) -> str | None:
    return t.isoformat(timespec="seconds") if t else None


async def _sleep_until(t: datetime) -> None:
    delay = (t - datetime.now()).total_seconds()
    if delay > 0:
        await asyncio.sleep(delay)


async def run(session: Session, settings: Settings, fleet: FleetClient | None = None, notifier: Notifier | None = None) -> Session:
    s = settings
    fleet = fleet or FleetClient(s)
    notifier = notifier or Notifier(s)
    started_climate = False
    started_sentry = False

    async def cmd(coro_fn, *args, desc: str):
        if session.dry_run:
            session.note(f"[dry-run] would: {desc}")
            return
        try:
            await coro_fn(*args)
            session.note(desc)
        except FleetError as e:
            session.note(f"FAILED {desc}: {e}")

    try:
        # 1. Work out lead time from the weather, without waking the car.
        pre_fog = None
        pre_w = None
        pre_loc = location.resolve(s, session.lat, session.lon)
        if pre_loc:
            try:
                pre_w = await weather.current(pre_loc.lat, pre_loc.lon)
                pre_fog = fogmod.assess(pre_w)
                session.note(f"planning with weather at {pre_loc.describe()}: {pre_w.temp_c}°C, dew point {pre_w.dew_point_c}°C")
            except Exception as e:  # weather is best-effort
                session.note(f"weather lookup failed: {e}")
        else:
            session.note("no location yet; using the default lead time (send your phone's location to plan for fog/frost)")
        lead = planner.lead_minutes(pre_fog, pre_w.temp_c if pre_w else None, s.default_lead_min, s.defrost_lead_min)
        session.start_at = max(datetime.now(), session.departure - timedelta(minutes=lead))
        session.note(f"leaving for {session.label} at {session.departure:%H:%M}; starting at {session.start_at:%H:%M} (lead {lead} min)")

        session.status = "waiting"
        await _sleep_until(session.start_at)

        # 2. Wake the car and read its state once.
        session.status = "checking"
        if not session.dry_run:
            await fleet.wake()
        snap = await fleet.snapshot()
        session.woke_at = datetime.now()
        session.note(
            f"car: inside {snap.inside_temp}°C, outside {snap.outside_temp}°C, battery {snap.battery_level}%, "
            f"climate {'on' if snap.is_climate_on else 'off'}"
        )
        if snap.is_driving:
            session.note("car is already driving; nothing to do")
            session.status = "done"
            return session

        want_sentry = s.sentry_on_depart if session.sentry is None else session.sentry
        if want_sentry and not snap.sentry_mode:
            await cmd(fleet.sentry_mode, True, desc="sentry mode on")
            started_sentry = not session.dry_run

        # 3. Weather at the car's own GPS position; otherwise keep the pre-wake weather.
        w = pre_w
        if snap.latitude is not None and snap.longitude is not None:
            location.save_car_location(s, snap.latitude, snap.longitude)
            try:
                w = await weather.current(snap.latitude, snap.longitude)
            except Exception as e:
                session.note(f"weather lookup failed: {e}")
        fog = fogmod.assess(w, cabin_temp_c=snap.inside_temp) if w else None
        if fog:
            session.note(f"fog risk: exterior {fog.exterior.name}, interior {fog.interior.name}, frost={fog.frost}")
            session.glass = "Frost" if fog.frost else ("Clear" if fog.overall.name in ("NONE", "LOW") else f"Fog risk {fog.overall.name.lower()}")

        # 4. Camera check (only if a TeslaCam folder is available).
        report = None
        worst_frame: Path | None = None
        if s.teslacam_path:
            try:
                frames = cameras.frames_from_path(
                    Path(s.teslacam_path).expanduser(),
                    Path(tempfile.mkdtemp(prefix="clearview-")),
                    max_age=timedelta(minutes=s.clip_max_age_min),
                )
                if frames:
                    report = await asyncio.to_thread(vision.analyze, frames, s.anthropic_model)
                    session.note(f"vision: {report.summary}")
                    dirty = report.dirty_cameras()
                    if dirty:
                        worst = max(dirty, key=lambda c: c.obstruction_pct)
                        worst_frame = next((f.path for f in frames if f.camera == worst.camera), None)
                else:
                    session.note(f"no clips newer than {s.clip_max_age_min} min; skipping camera check")
            except Exception as e:
                session.note(f"camera check failed: {e}")

        session.checked_at = datetime.now()
        if not fog:
            session.glass = "No weather data"
        if report:
            session.glass = f"{session.glass or 'Checked'} · cameras checked"

        # 5. Decide and act.
        plan = planner.decide(
            fog=fog,
            vision=report,
            inside_c=snap.inside_temp,
            outside_c=w.temp_c if w else snap.outside_temp,
            battery_pct=snap.battery_level,
            target_c=s.target_temp_c,
            min_battery_pct=s.min_battery_pct,
            seat_heat_below_c=s.seat_heat_below_c,
        )
        session.plan = plan.describe()

        if plan.climate_on:
            await cmd(fleet.set_temps, s.target_temp_c, desc=f"set temps {s.target_temp_c}°C")
            await cmd(fleet.climate_on, desc="climate on")
            started_climate = not session.dry_run
            if plan.max_defrost:
                await cmd(fleet.max_defrost, True, desc="max defrost on (front + rear)")
            if plan.seat_heaters:
                await cmd(fleet.seat_heater, 0, 3, desc="driver seat heater high")
            if plan.steering_heater:
                await cmd(fleet.steering_wheel_heater, True, desc="steering wheel heater on")

        session.acted_at = datetime.now()
        why = "; ".join(plan.reasons) or "—"
        await notifier.send(
            f"🚗 {session.label} at {session.departure:%H:%M}: {plan.describe()}",
            f"Why: {why}" + (f"\nCameras: {report.summary}" if report else ""),
            tags=["car"],
        )

        alerts = planner.camera_alerts(report)
        try:
            alerts += planner.car_camera_alerts(await fleet.recent_alerts())
        except FleetError as e:
            session.note(f"recent_alerts failed: {e}")
        if alerts:
            await notifier.send(
                "🧽 Clean camera(s) before driving", "\n".join(alerts),
                priority="high", tags=["warning"], attachment=worst_frame,
            )

        # 6. Auto-off if you don't leave: undo whatever clearview switched on.
        if started_climate or started_sentry:
            session.status = "climate_running" if started_climate else "sentry_running"
            await _sleep_until(session.departure + timedelta(minutes=s.stop_grace_min))
            try:
                snap2 = await fleet.snapshot()
                if not snap2.is_driving:
                    stopped = []
                    if started_climate and snap2.is_climate_on:
                        await cmd(fleet.max_defrost, False, desc="max defrost off")
                        await cmd(fleet.climate_off, desc="climate off (no departure detected)")
                        stopped.append("climate")
                    if started_sentry and snap2.sentry_mode:
                        await cmd(fleet.sentry_mode, False, desc="sentry mode off (no departure detected)")
                        stopped.append("Sentry Mode")
                    if stopped:
                        await notifier.send(
                            "🛑 Climate stopped" if started_climate and "climate" in stopped else "🛑 Sentry Mode off",
                            f"You didn't leave for {session.label}; {' and '.join(stopped)} turned off to save battery.")
            except VehicleAsleep:
                session.note("car asleep at grace check; climate and Sentry already off")

        session.status = "done"
        return session

    except asyncio.CancelledError:
        session.status = "cancelled"
        session.note("cancelled")
        if started_climate:
            try:
                await fleet.max_defrost(False)
                await fleet.climate_off()
                session.note("climate off (cancel)")
            except FleetError as e:
                session.note(f"failed to stop climate on cancel: {e}")
        if started_sentry:
            try:
                await fleet.sentry_mode(False)
                session.note("sentry mode off (cancel)")
            except FleetError as e:
                session.note(f"failed to turn off sentry on cancel: {e}")
        raise
    except Exception as e:
        session.status = "error"
        session.note(f"error: {e}")
        await notifier.send("⚠️ clearview error", f"{session.label}: {e}", priority="high")
        return session
