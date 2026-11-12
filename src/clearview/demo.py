"""A simulated car for CLEARVIEW_DEMO=1: try the dashboard without touching the real car.

It behaves like FleetClient (same methods), keeps its state in memory, and adds a short
delay to wakes and commands so the dashboard's pending states are visible.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone

from .config import Settings
from .fleet import VehicleSnapshot


class DemoFleet:
    def __init__(self, settings: Settings, delay_s: float = 1.2):
        self.s = settings
        self.delay = delay_s
        lat = settings.home_lat if settings.home_lat is not None else 37.7793
        lon = settings.home_lon if settings.home_lon is not None else -122.4193
        self.state = {
            "online": False,
            "inside_temp": 4.0,
            "outside_temp": 1.5,
            "is_climate_on": False,
            "front_defrost": False,
            "rear_defrost": False,
            "battery_level": 78,
            "battery_range": 241.0,
            "locked": True,
            "sentry_mode": False,
            "seat_heater_left": 0,
            "seat_heater_right": 0,
            "steering_wheel_heater": False,
            "driver_temp_setting": settings.target_temp_c,
            # parked ~55 m (180 ft) from home
            "latitude": lat + 0.0004,
            "longitude": lon - 0.0003,
        }

    async def _pause(self) -> None:
        await asyncio.sleep(self.delay)

    async def list_vehicles(self) -> list[dict]:
        return [{"vin": "DEMO0000000000001", "display_name": "Model Y (demo)",
                 "state": "online" if self.state["online"] else "asleep"}]

    async def vin(self) -> str:
        return "DEMO0000000000001"

    async def wake(self, timeout_s: int = 90) -> None:
        if not self.state["online"]:
            await self._pause()
            self.state["online"] = True

    async def snapshot(self) -> VehicleSnapshot:
        st = self.state
        if st["is_climate_on"]:  # cabin creeps toward the set temperature on each read
            st["inside_temp"] = round(min(st["driver_temp_setting"], st["inside_temp"] + 3.5), 1)
        return VehicleSnapshot(
            vin="DEMO0000000000001", inside_temp=st["inside_temp"], outside_temp=st["outside_temp"],
            is_climate_on=st["is_climate_on"], front_defrost=st["front_defrost"], rear_defrost=st["rear_defrost"],
            battery_level=st["battery_level"], shift_state=None, latitude=st["latitude"], longitude=st["longitude"],
            sentry_mode=st["sentry_mode"], locked=st["locked"], battery_range=st["battery_range"],
            seat_heater_left=st["seat_heater_left"], seat_heater_right=st["seat_heater_right"],
            steering_wheel_heater=st["steering_wheel_heater"], driver_temp_setting=st["driver_temp_setting"],
        )

    async def recent_alerts(self) -> list[dict]:
        now = datetime.now(timezone.utc)
        iso = lambda d: (now - d).isoformat(timespec="seconds")  # noqa: E731
        return [
            {"name": "APP_w304_pillarCameraBlocked", "user_text": "Right pillar camera blocked", "time": iso(timedelta(hours=13))},
            {"name": "VCSEC_a120_tirePressureLow", "user_text": "Tire pressure low (rear left)", "time": iso(timedelta(days=2))},
            {"name": "APP_w218_cameraViewLimited", "user_text": "Camera view limited by frost", "time": iso(timedelta(days=3))},
        ]

    async def _cmd(self, **changes) -> None:
        await self.wake()
        await self._pause()
        self.state.update(changes)

    async def climate_on(self) -> None:
        await self._cmd(is_climate_on=True)

    async def climate_off(self) -> None:
        await self._cmd(is_climate_on=False, front_defrost=False, rear_defrost=False)

    async def set_temps(self, celsius: float) -> None:
        await self._cmd(driver_temp_setting=celsius)

    async def max_defrost(self, on: bool) -> None:
        await self._cmd(front_defrost=on, rear_defrost=on, **({"is_climate_on": True} if on else {}))

    async def seat_heater(self, seat: int, level: int) -> None:
        await self._cmd(**{"seat_heater_left" if seat == 0 else "seat_heater_right": level})

    async def steering_wheel_heater(self, on: bool) -> None:
        await self._cmd(steering_wheel_heater=on)

    async def sentry_mode(self, on: bool) -> None:
        await self._cmd(sentry_mode=on)
