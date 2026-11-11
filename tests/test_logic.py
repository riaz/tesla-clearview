from datetime import datetime, timedelta
from pathlib import Path

from clearview import cameras, fog, planner
from clearview.config import Settings
from clearview.fleet import VehicleSnapshot
from clearview.parse import parse_departure
from clearview.session import Session, run
from clearview.vision import CameraReport, Obstruction, Severity, VisionReport
from clearview.weather import Weather


def W(**kw):
    base = dict(temp_c=10, humidity_pct=60, dew_point_c=2.5, cloud_cover_pct=50, precipitation_mm=0, wind_kmh=5, is_day=True)
    return Weather(**{**base, **kw})


# --- fog -------------------------------------------------------------------

def test_clear_calm_night_near_freezing_is_frost():
    a = fog.assess(W(temp_c=1.5, dew_point_c=0.5, humidity_pct=93, cloud_cover_pct=0, is_day=False))
    assert a.frost and a.exterior == fog.Risk.HIGH


def test_warm_dry_day_no_risk():
    a = fog.assess(W(temp_c=22, dew_point_c=8, humidity_pct=40))
    assert a.overall == fog.Risk.NONE and not a.frost


def test_cold_rain_interior_risk():
    a = fog.assess(W(temp_c=4, dew_point_c=3.5, humidity_pct=97, precipitation_mm=1.2, cloud_cover_pct=100))
    assert a.interior == fog.Risk.HIGH


# --- planner ----------------------------------------------------------------

KW = dict(target_c=21, min_battery_pct=20, seat_heat_below_c=5)


def test_frost_triggers_max_defrost_and_seat_heat():
    f = fog.assess(W(temp_c=-2, dew_point_c=-3, humidity_pct=92, cloud_cover_pct=0, is_day=False))
    p = planner.decide(fog=f, vision=None, inside_c=-1, outside_c=-2, battery_pct=70, **KW)
    assert p.max_defrost and p.climate_on and p.seat_heaters


def test_low_battery_skips():
    p = planner.decide(fog=None, vision=None, inside_c=0, outside_c=0, battery_pct=10, **KW)
    assert not p.climate_on and "battery" in p.skip_reason


def test_comfortable_no_action():
    p = planner.decide(fog=None, vision=None, inside_c=20, outside_c=18, battery_pct=80, **KW)
    assert not p.climate_on and p.skip_reason


def test_vision_fog_overrides_weather():
    v = VisionReport(cameras=[], windscreen_fog=Severity.heavy, summary="fogged")
    p = planner.decide(fog=None, vision=v, inside_c=20, outside_c=18, battery_pct=80, **KW)
    assert p.max_defrost


def test_lead_time():
    frost = fog.assess(W(temp_c=-3, dew_point_c=-4, cloud_cover_pct=0, is_day=False))
    assert planner.lead_minutes(frost, -3, 10, 20) == 20
    assert planner.lead_minutes(None, 15, 10, 20) == 10


def test_camera_alerts_sorted_and_thresholded():
    v = VisionReport(
        cameras=[
            CameraReport(camera="left_repeater", obstruction_pct=70, obstruction_type=Obstruction.dirt_mud, glass_fog=Severity.none, needs_cleaning=True, note="mud"),
            CameraReport(camera="back", obstruction_pct=10, obstruction_type=Obstruction.water_droplets, glass_fog=Severity.none, needs_cleaning=False, note="drops"),
            CameraReport(camera="right_pillar", obstruction_pct=40, obstruction_type=Obstruction.snow, glass_fog=Severity.none, needs_cleaning=True, note="snow"),
        ],
        windscreen_fog=Severity.none, summary="",
    )
    alerts = planner.camera_alerts(v)
    assert len(alerts) == 2 and alerts[0].startswith("left_repeater: 70%")


def test_car_camera_alerts():
    alerts = [{"name": "APP_w_camera_blocked", "user_text": "Left pillar camera blocked or blinded"}, {"name": "VCSEC_x", "user_text": "Tire pressure low"}]
    assert planner.car_camera_alerts(alerts) == ["car alert: Left pillar camera blocked or blinded"]


# --- parse ------------------------------------------------------------------

NOW = datetime(2026, 11, 11, 7, 0)


def test_parse_in_minutes():
    d = parse_departure("I will leave for work in 10 min", now=NOW)
    assert d.label == "work" and d.at == NOW + timedelta(minutes=10)


def test_parse_at_time_rolls_over():
    d = parse_departure("gym at 6:30am", now=NOW)
    assert d.at == datetime(2026, 11, 12, 6, 30)


def test_parse_hours():
    assert parse_departure("leaving to the airport in 1.5 hours", now=NOW).at == NOW + timedelta(minutes=90)


# --- cameras ----------------------------------------------------------------

def test_latest_per_camera(tmp_path: Path):
    for name in ["2026-11-11_06-00-00-front.mp4", "2026-11-11_06-50-00-front.mp4", "2026-11-11_06-50-00-back.mp4", "junk.mp4"]:
        (tmp_path / "SentryClips" / "x").mkdir(parents=True, exist_ok=True)
        (tmp_path / "SentryClips" / "x" / name).write_bytes(b"")
    latest = cameras.latest_per_camera(cameras.find_clips(tmp_path), max_age=timedelta(minutes=30), now=NOW)
    assert set(latest) == {"front", "back"}
    assert latest["front"].timestamp == datetime(2026, 11, 11, 6, 50)


# --- session (fake car) -----------------------------------------------------

class FakeFleet:
    def __init__(self, snap):
        self.snap, self.calls = snap, []

    async def wake(self): self.calls.append("wake")
    async def snapshot(self): return self.snap
    async def recent_alerts(self): return []
    async def set_temps(self, c): self.calls.append(f"temps {c}")
    async def climate_on(self): self.calls.append("climate_on"); self.snap.is_climate_on = True
    async def climate_off(self): self.calls.append("climate_off")
    async def max_defrost(self, on): self.calls.append(f"defrost {on}")
    async def seat_heater(self, seat, lvl): self.calls.append("seat")
    async def steering_wheel_heater(self, on): self.calls.append("wheel")


class FakeNotifier:
    def __init__(self): self.msgs = []
    async def send(self, title, message, **kw): self.msgs.append(title)


def snap(**kw):
    base = dict(vin="X", inside_temp=2.0, outside_temp=1.0, is_climate_on=False, front_defrost=False, rear_defrost=False,
                battery_level=80, shift_state=None, latitude=None, longitude=None, sentry_mode=False)
    return VehicleSnapshot(**{**base, **kw})


async def test_session_cold_car_runs_and_auto_stops():
    s = Settings(_env_file=None, stop_grace_min=0, home_lat=None, home_lon=None, teslacam_path="")
    fleet, n = FakeFleet(snap()), FakeNotifier()
    sess = await run(Session(label="work", departure=datetime.now()), s, fleet=fleet, notifier=n)
    assert sess.status == "done"
    assert "climate_on" in fleet.calls and "seat" in fleet.calls
    assert fleet.calls[-1] == "climate_off"  # not driving after grace -> stopped
    assert any("Climate stopped" in m for m in n.msgs)


async def test_session_skips_when_driving():
    s = Settings(_env_file=None, stop_grace_min=0, teslacam_path="")
    fleet = FakeFleet(snap(shift_state="D"))
    sess = await run(Session(label="x", departure=datetime.now()), s, fleet=fleet, notifier=FakeNotifier())
    assert sess.status == "done" and "climate_on" not in fleet.calls
