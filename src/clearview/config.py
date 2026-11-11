"""Settings, loaded from environment variables or a `.env` file."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

CONFIG_DIR = Path.home() / ".config" / "clearview"

FLEET_REGIONS = {
    "na": "https://fleet-api.prd.na.vn.cloud.tesla.com",  # North America + Asia-Pacific
    "eu": "https://fleet-api.prd.eu.vn.cloud.tesla.com",  # Europe, Middle East, Africa
    "cn": "https://fleet-api.prd.cn.vn.cloud.tesla.cn",
}


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    # --- Tesla developer app (developer.tesla.com) ---
    tesla_client_id: str = ""
    tesla_client_secret: str = ""
    tesla_redirect_uri: str = "http://localhost:8765/callback"
    tesla_domain: str = ""  # domain hosting /.well-known/appspecific/com.tesla.3p.public-key.pem
    tesla_region: str = "na"
    tesla_scopes: str = "openid offline_access vehicle_device_data vehicle_cmds vehicle_location"
    tesla_vin: str = ""  # optional; defaults to the first vehicle on the account

    # Signed commands go through Tesla's vehicle-command http proxy (see docker-compose.yml).
    # Leave empty only for pre-2021 Model S/X that still accept unsigned REST commands.
    tesla_command_proxy_url: str = "https://localhost:4443"
    tesla_proxy_ca_cert: str = "config/tls-cert.pem"

    token_file: Path = CONFIG_DIR / "tokens.json"

    # --- Vision (Claude) ---
    anthropic_model: str = "claude-opus-5-5"
    # Folder holding TeslaCam clips (USB drive, or a folder a TeslaUSB-style Pi syncs to).
    teslacam_path: str = ""
    clip_max_age_min: int = 30  # ignore clips older than this when judging current conditions

    # --- Weather / location ---
    home_lat: float | None = None  # used before waking the car to plan lead time
    home_lon: float | None = None

    # --- Climate preferences ---
    target_temp_c: float = 21.0
    min_battery_pct: int = 20  # never precondition below this
    default_lead_min: int = 10
    defrost_lead_min: int = 20
    stop_grace_min: int = 15  # stop climate this long after departure if the car hasn't moved
    seat_heat_below_c: float = 5.0

    # --- Notifications (ntfy.sh or self-hosted ntfy) ---
    ntfy_server: str = "https://ntfy.sh"
    ntfy_topic: str = ""
    ntfy_token: str = ""

    # --- Trigger API ---
    api_token: str = Field(default="", alias="CLEARVIEW_API_TOKEN")
    api_host: str = "127.0.0.1"
    api_port: int = 8787

    @property
    def fleet_base(self) -> str:
        return FLEET_REGIONS[self.tesla_region.lower()]


@lru_cache
def get_settings() -> Settings:
    return Settings()
