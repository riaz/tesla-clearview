"""Thin async client for the Tesla Fleet API.

Reads go straight to the Fleet API. Commands go through Tesla's vehicle-command
http proxy, which signs them with your virtual key (required on all modern cars).

Every call here costs Fleet API credit and `wake_up` drains the 12V/HV battery a
little, so the rest of the app calls this sparingly: once to wake, once to read,
a handful of commands, and one final check.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from typing import Any

import httpx

from . import auth
from .config import Settings

log = logging.getLogger(__name__)


class FleetError(RuntimeError):
    pass


class VehicleAsleep(FleetError):
    pass


@dataclass
class VehicleSnapshot:
    """The subset of `vehicle_data` that clearview uses."""

    vin: str
    inside_temp: float | None
    outside_temp: float | None
    is_climate_on: bool
    front_defrost: bool
    rear_defrost: bool
    battery_level: int | None
    shift_state: str | None
    latitude: float | None
    longitude: float | None
    sentry_mode: bool | None

    @property
    def is_driving(self) -> bool:
        return self.shift_state in ("D", "R", "N")

    @classmethod
    def from_api(cls, vin: str, d: dict[str, Any]) -> "VehicleSnapshot":
        climate = d.get("climate_state") or {}
        drive = d.get("drive_state") or {}
        charge = d.get("charge_state") or {}
        vstate = d.get("vehicle_state") or {}
        return cls(
            vin=vin,
            inside_temp=climate.get("inside_temp"),
            outside_temp=climate.get("outside_temp"),
            is_climate_on=bool(climate.get("is_climate_on")),
            front_defrost=bool(climate.get("is_front_defroster_on")),
            rear_defrost=bool(climate.get("is_rear_defroster_on")),
            battery_level=charge.get("battery_level"),
            shift_state=drive.get("shift_state"),
            latitude=drive.get("latitude"),
            longitude=drive.get("longitude"),
            sentry_mode=vstate.get("sentry_mode"),
        )


class FleetClient:
    def __init__(self, settings: Settings):
        self.s = settings
        self._vin = settings.tesla_vin or None

    def _headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {auth.access_token(self.s)}"}

    async def _request(self, method: str, url: str, *, verify: Any = True, **kw) -> dict:
        async with httpx.AsyncClient(timeout=30, verify=verify) as c:
            r = await c.request(method, url, headers=self._headers(), **kw)
        if r.status_code == 408:
            raise VehicleAsleep("Vehicle is asleep/offline")
        if r.status_code >= 300:
            raise FleetError(f"{method} {url} -> {r.status_code}: {r.text}")
        return r.json()

    async def list_vehicles(self) -> list[dict]:
        return (await self._request("GET", f"{self.s.fleet_base}/api/1/vehicles"))["response"]

    async def vin(self) -> str:
        if not self._vin:
            vehicles = await self.list_vehicles()
            if not vehicles:
                raise FleetError("No vehicles on this account")
            self._vin = vehicles[0]["vin"]
        return self._vin

    async def wake(self, timeout_s: int = 90) -> None:
        vin = await self.vin()
        deadline = asyncio.get_event_loop().time() + timeout_s
        delay = 3.0
        while True:
            r = await self._request("POST", f"{self.s.fleet_base}/api/1/vehicles/{vin}/wake_up")
            if r["response"].get("state") == "online":
                return
            if asyncio.get_event_loop().time() > deadline:
                raise FleetError("Vehicle did not wake up in time")
            await asyncio.sleep(delay)
            delay = min(delay * 1.5, 10)

    async def snapshot(self) -> VehicleSnapshot:
        vin = await self.vin()
        endpoints = "climate_state;drive_state;charge_state;vehicle_state;location_data"
        r = await self._request(
            "GET",
            f"{self.s.fleet_base}/api/1/vehicles/{vin}/vehicle_data",
            params={"endpoints": endpoints},
        )
        return VehicleSnapshot.from_api(vin, r["response"])

    async def recent_alerts(self) -> list[dict]:
        vin = await self.vin()
        r = await self._request("GET", f"{self.s.fleet_base}/api/1/vehicles/{vin}/recent_alerts")
        resp = r.get("response") or {}
        return resp.get("recent_alerts", []) if isinstance(resp, dict) else []

    async def command(self, name: str, body: dict | None = None) -> dict:
        vin = await self.vin()
        if self.s.tesla_command_proxy_url:
            base = self.s.tesla_command_proxy_url.rstrip("/")
            verify: Any = self.s.tesla_proxy_ca_cert or True
        else:
            base, verify = self.s.fleet_base, True
        r = await self._request(
            "POST", f"{base}/api/1/vehicles/{vin}/command/{name}", json=body or {}, verify=verify
        )
        resp = r.get("response") or {}
        if resp.get("result") is False:
            raise FleetError(f"Command {name} rejected: {resp.get('reason')}")
        log.info("command %s ok", name)
        return resp

    # --- convenience wrappers -------------------------------------------------

    async def climate_on(self) -> None:
        await self.command("auto_conditioning_start")

    async def climate_off(self) -> None:
        await self.command("auto_conditioning_stop")

    async def set_temps(self, celsius: float) -> None:
        await self.command("set_temps", {"driver_temp": celsius, "passenger_temp": celsius})

    async def max_defrost(self, on: bool) -> None:
        # "Defrost car": max heat, front + rear defrosters, airflow to windscreen/demist vents.
        await self.command("set_preconditioning_max", {"on": on, "manual_override": on})

    async def seat_heater(self, seat: int, level: int) -> None:
        await self.command("remote_seat_heater_request", {"seat_position": seat, "seat_heater_level": level})

    async def steering_wheel_heater(self, on: bool) -> None:
        await self.command("remote_steering_wheel_heater_request", {"on": on})

    async def sentry_mode(self, on: bool) -> None:
        # Keeps the car awake with cameras watching; costs battery. Disengages when you drive.
        await self.command("set_sentry_mode", {"on": on})
