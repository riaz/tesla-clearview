"""Push notifications via ntfy (install the ntfy app on your phone and subscribe to your topic)."""

from __future__ import annotations

import base64
import logging
from pathlib import Path

import httpx

from .config import Settings

log = logging.getLogger(__name__)


def _hdr(value: str) -> str:
    """HTTP headers must be ASCII; ntfy accepts RFC 2047 encoded-words for anything else."""
    try:
        value.encode("ascii")
        return value
    except UnicodeEncodeError:
        return "=?UTF-8?B?" + base64.b64encode(value.encode("utf-8")).decode() + "?="


class Notifier:
    def __init__(self, settings: Settings):
        self.s = settings

    async def send(
        self,
        title: str,
        message: str,
        priority: str = "default",
        tags: list[str] | None = None,
        attachment: Path | None = None,
    ) -> None:
        log.info("NOTIFY [%s] %s: %s", priority, title, message)
        if not self.s.ntfy_topic:
            print(f"\n🔔 {title}\n{message}\n")
            return
        url = f"{self.s.ntfy_server.rstrip('/')}/{self.s.ntfy_topic}"
        headers = {"Title": _hdr(title), "Priority": priority}
        if tags:
            headers["Tags"] = ",".join(tags)
        if self.s.ntfy_token:
            headers["Authorization"] = f"Bearer {self.s.ntfy_token}"
        try:
            async with httpx.AsyncClient(timeout=20) as c:
                if attachment and attachment.exists():
                    # ntfy allows an attachment body with the text in a header (UTF-8 is RFC 2047-encoded).
                    headers["Filename"] = attachment.name
                    headers["Message"] = _hdr(message.replace("\n", "\\n"))
                    r = await c.put(url, content=attachment.read_bytes(), headers=headers)
                else:
                    r = await c.post(url, content=message.encode(), headers=headers)
                r.raise_for_status()
        except httpx.HTTPError as e:
            log.warning("ntfy failed: %s", e)
