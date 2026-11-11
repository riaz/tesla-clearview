"""Parse phrases like "leaving for work in 10 min" or "gym at 7:45am"."""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timedelta

_IN = re.compile(r"\bin\s+(?P<n>\d+(?:\.\d+)?)\s*(?P<unit>m|min|mins|minutes?|h|hr|hrs|hours?)\b", re.I)
_AT = re.compile(r"\bat\s+(?P<h>\d{1,2})(?::(?P<m>\d{2}))?\s*(?P<ampm>am|pm)?\b", re.I)
_FOR = re.compile(r"\b(?:for|to)\s+(?P<label>.+?)(?=\s+(?:in|at)\s+\d|$)", re.I)


@dataclass
class Departure:
    label: str
    at: datetime


def parse_departure(text: str, now: datetime | None = None) -> Departure:
    now = now or datetime.now()
    text = text.strip()
    when: datetime | None = None

    if m := _IN.search(text):
        n = float(m["n"])
        minutes = n * 60 if m["unit"].lower().startswith("h") else n
        when = now + timedelta(minutes=minutes)
    elif m := _AT.search(text):
        h, mi = int(m["h"]), int(m["m"] or 0)
        if m["ampm"]:
            h = h % 12 + (12 if m["ampm"].lower() == "pm" else 0)
        when = now.replace(hour=h, minute=mi, second=0, microsecond=0)
        if when <= now:
            when += timedelta(days=1)
    if when is None:
        raise ValueError(f"Couldn't find a time in {text!r}. Try 'in 10 min' or 'at 7:45am'.")

    label = "drive"
    if m := _FOR.search(text):
        label = m["label"].strip(" .,!")
    return Departure(label=label, at=when)
