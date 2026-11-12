"""Where is the car? Used before waking it, to pick the weather for the lead-time plan.

Order of preference:
1. A location sent with the request: your phone's GPS from the dashboard or an iOS Shortcut.
   When you're leaving, your phone is near the car, wherever that is (home, hotel, office).
2. The car's last known position, saved every time clearview reads the car.
3. HOME_LAT / HOME_LON, if set.

After the car wakes, its own GPS always wins.
"""

from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass

from .config import Settings


@dataclass
class Location:
    lat: float
    lon: float
    source: str  # "phone" | "car" | "car_last_known" | "home"
    at: float | None = None  # unix time the position was recorded

    def describe(self) -> str:
        label = {
            "phone": "your phone's location",
            "car": "the car's GPS",
            "car_last_known": "the car's last known position",
            "home": "HOME_LAT/HOME_LON",
        }[self.source]
        if self.source == "car_last_known" and self.at:
            hours = (time.time() - self.at) / 3600
            label += f" ({hours:.0f} h ago)" if hours >= 1 else f" ({max(1, round(hours * 60))} min ago)"
        return label


def _path(settings: Settings):
    return settings.token_file.parent / "last_location.json"


def save_car_location(settings: Settings, lat: float | None, lon: float | None) -> None:
    if lat is None or lon is None:
        return
    p = _path(settings)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".tmp")
    tmp.write_text(json.dumps({"lat": lat, "lon": lon, "at": time.time()}))
    os.chmod(tmp, 0o600)
    tmp.replace(p)


def last_car_location(settings: Settings) -> Location | None:
    try:
        d = json.loads(_path(settings).read_text())
        return Location(float(d["lat"]), float(d["lon"]), "car_last_known", d.get("at"))
    except (OSError, ValueError, KeyError, TypeError):
        return None


def resolve(settings: Settings, lat: float | None = None, lon: float | None = None) -> Location | None:
    """Best location without waking the car."""
    if lat is not None and lon is not None:
        return Location(lat, lon, "phone", time.time())
    if loc := last_car_location(settings):
        return loc
    if settings.home_lat is not None and settings.home_lon is not None:
        return Location(settings.home_lat, settings.home_lon, "home")
    return None
