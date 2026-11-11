"""Pure decision logic: conditions in, climate plan out. No I/O, so it's easy to test."""

from __future__ import annotations

from dataclasses import dataclass, field

from .fog import FogAssessment, Risk
from .vision import Severity, VisionReport

_SEV_RANK = {Severity.none: 0, Severity.light: 1, Severity.moderate: 2, Severity.heavy: 3}


@dataclass
class ClimatePlan:
    climate_on: bool = False
    max_defrost: bool = False  # front + rear defrost, airflow to windscreen/demist vents
    target_temp_c: float | None = None
    seat_heaters: bool = False
    steering_heater: bool = False
    skip_reason: str | None = None
    reasons: list[str] = field(default_factory=list)

    def describe(self) -> str:
        if self.skip_reason:
            return f"No climate: {self.skip_reason}"
        parts = []
        if self.max_defrost:
            parts.append("Max defrost (front + rear)")
        elif self.climate_on:
            parts.append(f"Climate {self.target_temp_c:.0f}°C")
        if self.seat_heaters:
            parts.append("seat heaters")
        if self.steering_heater:
            parts.append("steering wheel heat")
        return ", ".join(parts) or "No action"


def lead_minutes(fog: FogAssessment | None, outside_c: float | None, default: int, defrost: int) -> int:
    """How far before departure to start. Decided before waking the car."""
    if fog and (fog.frost or fog.overall >= Risk.HIGH):
        return defrost
    if fog and fog.overall >= Risk.MEDIUM:
        return max(default, (default + defrost) // 2)
    if outside_c is not None and (outside_c < 0 or outside_c > 32):
        return max(default, (default + defrost) // 2)
    return default


def decide(
    *,
    fog: FogAssessment | None,
    vision: VisionReport | None,
    inside_c: float | None,
    outside_c: float | None,
    battery_pct: int | None,
    target_c: float,
    min_battery_pct: int,
    seat_heat_below_c: float,
) -> ClimatePlan:
    plan = ClimatePlan(target_temp_c=target_c)

    if battery_pct is not None and battery_pct < min_battery_pct:
        plan.skip_reason = f"battery {battery_pct}% is below the {min_battery_pct}% floor"
        return plan

    # Fog/frost → max defrost
    if fog and (fog.frost or fog.exterior >= Risk.HIGH or fog.interior >= Risk.HIGH):
        plan.max_defrost = True
        plan.reasons += fog.reasons
    if vision and _SEV_RANK[vision.windscreen_fog] >= _SEV_RANK[Severity.moderate]:
        plan.max_defrost = True
        plan.reasons.append(f"cameras see {vision.windscreen_fog.value} fog on the windscreen")

    # Medium fog risk: A/C on dries the air even without max defrost
    if not plan.max_defrost:
        if fog and fog.overall >= Risk.MEDIUM:
            plan.climate_on = True
            plan.reasons.append("moderate fog risk, A/C on to dry the cabin air")
        if vision and vision.windscreen_fog == Severity.light:
            plan.climate_on = True
            plan.reasons.append("light windscreen fog seen")

    # Comfort: only if the cabin is meaningfully off target
    if inside_c is not None and abs(inside_c - target_c) >= 4:
        plan.climate_on = True
        plan.reasons.append(f"cabin {inside_c:.0f}°C vs target {target_c:.0f}°C")
    elif inside_c is None and outside_c is not None and abs(outside_c - target_c) >= 8:
        plan.climate_on = True
        plan.reasons.append(f"outside {outside_c:.0f}°C")

    if plan.max_defrost:
        plan.climate_on = True

    cold = outside_c if outside_c is not None else inside_c
    if plan.climate_on and cold is not None and cold <= seat_heat_below_c:
        plan.seat_heaters = True
        plan.steering_heater = True

    if not plan.climate_on:
        plan.skip_reason = "cabin is comfortable and no fog risk"
    return plan


_CAM_WORDS = ("camera", "cam_", "_cam", "blind", "obstruct", "occlu", "repeater", "pillar")


def car_camera_alerts(alerts: list[dict]) -> list[str]:
    """Pick the car's own camera-blocked/blinded warnings out of `recent_alerts`."""
    out = []
    for a in alerts:
        text = f"{a.get('name', '')} {a.get('user_text', '')}".lower()
        if any(w in text for w in _CAM_WORDS):
            out.append(f"car alert: {a.get('user_text') or a.get('name')}")
    return list(dict.fromkeys(out))


def camera_alerts(vision: VisionReport | None, threshold: int = 30) -> list[str]:
    if not vision:
        return []
    out = []
    for c in sorted(vision.dirty_cameras(threshold), key=lambda c: -c.obstruction_pct):
        out.append(f"{c.camera}: {c.obstruction_pct}% obstructed ({c.obstruction_type.value.replace('_', ' ')}) — {c.note}")
    return out
