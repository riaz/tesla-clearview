"""Current conditions from Open-Meteo (free, no API key)."""

from __future__ import annotations

from dataclasses import dataclass

import httpx

URL = "https://api.open-meteo.com/v1/forecast"


@dataclass
class Weather:
    temp_c: float
    humidity_pct: float
    dew_point_c: float
    cloud_cover_pct: float
    precipitation_mm: float
    wind_kmh: float
    is_day: bool


async def current(lat: float, lon: float) -> Weather:
    # Round to ~1 km; exact position isn't needed for weather.
    params = {
        "latitude": round(lat, 2),
        "longitude": round(lon, 2),
        "current": "temperature_2m,relative_humidity_2m,dew_point_2m,cloud_cover,precipitation,wind_speed_10m,is_day",
    }
    async with httpx.AsyncClient(timeout=15) as c:
        r = await c.get(URL, params=params)
        r.raise_for_status()
    cur = r.json()["current"]
    return Weather(
        temp_c=cur["temperature_2m"],
        humidity_pct=cur["relative_humidity_2m"],
        dew_point_c=cur["dew_point_2m"],
        cloud_cover_pct=cur["cloud_cover"],
        precipitation_mm=cur["precipitation"],
        wind_kmh=cur["wind_speed_10m"],
        is_day=bool(cur["is_day"]),
    )
