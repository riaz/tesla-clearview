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
    # The callback page on your Pages site, e.g. https://<user>.github.io/callback/ (see site/).
    tesla_redirect_uri: str = ""
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
    # Optional last-resort fallback. clearview normally uses your phone's location (sent with the
    # request) or the car's last known position; see location.py.
    home_lat: float | None = None
    home_lon: float | None = None

    # --- Climate preferences ---
    target_temp_c: float = 21.0
    min_battery_pct: int = 20  # never precondition below this
    default_lead_min: int = 10
    defrost_lead_min: int = 20
    stop_grace_min: int = 15  # stop climate this long after departure if the car hasn't moved
    seat_heat_below_c: float = 5.0
    # Turn Sentry Mode on when a departure session wakes the car (cameras record events, and
    # Live Camera is available in the Tesla app). Turned back off if you don't leave.
    sentry_on_depart: bool = False

    # --- Notifications (ntfy.sh or self-hosted ntfy) ---
    ntfy_server: str = "https://ntfy.sh"
    ntfy_topic: str = ""
    ntfy_token: str = ""

    # --- Trigger API ---
    api_token: str = Field(default="", alias="CLEARVIEW_API_TOKEN")
    api_host: str = "127.0.0.1"
    api_port: int = 8787
    # How your browser reaches this server, e.g. http://homeserver.local:8787. The Pages
    # callback bounces the Tesla login back here. Empty = use the URL the dashboard was opened on.
    public_url: str = Field(default="", alias="CLEARVIEW_PUBLIC_URL")

    @property
    def fleet_base(self) -> str:
        return FLEET_REGIONS[self.tesla_region.lower()]


@lru_cache
def get_settings() -> Settings:
    return Settings()
