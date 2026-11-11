"""Physics-based glass fog/frost risk. Works without cameras.

Glass parked outside cools below the air temperature by radiating to a clear sky,
most of all on calm, clear nights. When the glass gets colder than the dew point,
water condenses on the outside. Below 0 °C that water turns to frost. The inside
fogs up when the glass is cold and the cabin air is humid (wet clothes, breath,
rain).
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import IntEnum

from .weather import Weather


class Risk(IntEnum):
    NONE = 0
    LOW = 1
    MEDIUM = 2
    HIGH = 3


@dataclass
class FogAssessment:
    exterior: Risk  # dew / frost on the outside of the glass
    interior: Risk  # condensation on the inside
    frost: bool
    glass_temp_c: float
    reasons: list[str]

    @property
    def overall(self) -> Risk:
        return max(self.exterior, self.interior)


def estimate_glass_temp(air_c: float, cloud_pct: float, wind_kmh: float, is_day: bool) -> float:
    """Rough glass temperature after a long park outdoors."""
    if is_day:
        return air_c  # sun-warmed glass is about air temperature
    clear = 1.0 - min(max(cloud_pct, 0), 100) / 100.0
    calm = 1.0 if wind_kmh < 10 else 0.5 if wind_kmh < 20 else 0.2
    return air_c - 3.0 * clear * calm  # up to ~3 °C of radiative cooling


def assess(w: Weather, cabin_temp_c: float | None = None, parked_indoors: bool = False) -> FogAssessment:
    reasons: list[str] = []
    glass = w.temp_c if parked_indoors else estimate_glass_temp(w.temp_c, w.cloud_cover_pct, w.wind_kmh, w.is_day)

    # Exterior: how close the glass is to the dew point
    margin = glass - w.dew_point_c
    frost = glass <= 0.5 and margin <= 1.0
    if margin <= 0:
        exterior = Risk.HIGH
        reasons.append(f"glass ≈{glass:.1f}°C is at/below dew point {w.dew_point_c:.1f}°C")
    elif margin <= 1.5:
        exterior = Risk.MEDIUM
        reasons.append(f"glass ≈{glass:.1f}°C within {margin:.1f}°C of dew point")
    elif margin <= 3:
        exterior = Risk.LOW
    else:
        exterior = Risk.NONE
    if frost:
        exterior = Risk.HIGH
        reasons.append("frost likely (glass at or below freezing)")
    if w.precipitation_mm > 0 and w.temp_c <= 1:
        exterior = Risk.HIGH
        frost = True
        reasons.append("freezing precipitation")

    # Interior: cold glass and moist air
    interior = Risk.NONE
    if w.temp_c < 10:
        if w.humidity_pct >= 90 or w.precipitation_mm > 0:
            interior = Risk.HIGH if w.temp_c < 5 else Risk.MEDIUM
            reasons.append(f"cold ({w.temp_c:.0f}°C) and humid/wet ({w.humidity_pct:.0f}% RH)")
        elif w.humidity_pct >= 75:
            interior = Risk.MEDIUM if w.temp_c < 5 else Risk.LOW
            reasons.append(f"cool and moderately humid ({w.humidity_pct:.0f}% RH)")
    if cabin_temp_c is not None and cabin_temp_c - w.temp_c > 8 and interior >= Risk.LOW:
        interior = Risk(min(interior + 1, Risk.HIGH))
        reasons.append("cabin much warmer than outside")

    return FogAssessment(exterior=exterior, interior=interior, frost=frost, glass_temp_c=glass, reasons=reasons)
