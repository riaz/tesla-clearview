"""Find the newest TeslaCam clips and pull one still frame per camera.

The Fleet API has no camera endpoint. Sentry/Dashcam footage only lands on the
car's USB drive, under TeslaCam/{RecentClips,SavedClips,SentryClips}. Point
TESLACAM_PATH at one of:
  * the mounted USB drive, or
  * a folder that a Raspberry Pi USB-gadget setup (e.g. TeslaUSB) syncs over Wi-Fi.

Clip files are named like `2026-11-11_07-30-12-front.mp4` (local time).
"""

from __future__ import annotations

import re
import subprocess
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path

CLIP_RE = re.compile(r"(?P<ts>\d{4}-\d{2}-\d{2}_\d{2}-\d{2}-\d{2})-(?P<cam>[a-z_]+)\.mp4$")

# Cameras that look through the windscreen; fog on these suggests fog on the windscreen.
WINDSCREEN_CAMERAS = {"front"}
CAMERA_LABELS = {
    "front": "Front (behind windscreen)",
    "back": "Rear (above number plate)",
    "left_repeater": "Left repeater (front fender)",
    "right_repeater": "Right repeater (front fender)",
    "left_pillar": "Left B-pillar",
    "right_pillar": "Right B-pillar",
}


@dataclass
class Clip:
    path: Path
    camera: str
    timestamp: datetime


@dataclass
class Frame:
    camera: str
    path: Path
    timestamp: datetime

    @property
    def label(self) -> str:
        return CAMERA_LABELS.get(self.camera, self.camera)


def find_clips(root: Path) -> list[Clip]:
    clips = []
    for p in root.rglob("*.mp4"):
        m = CLIP_RE.search(p.name)
        if not m:
            continue
        ts = datetime.strptime(m["ts"], "%Y-%m-%d_%H-%M-%S")
        clips.append(Clip(path=p, camera=m["cam"], timestamp=ts))
    return clips


def latest_per_camera(clips: list[Clip], max_age: timedelta | None = None, now: datetime | None = None) -> dict[str, Clip]:
    now = now or datetime.now()
    best: dict[str, Clip] = {}
    for c in clips:
        if max_age is not None and now - c.timestamp > max_age:
            continue
        if c.camera not in best or c.timestamp > best[c.camera].timestamp:
            best[c.camera] = c
    return best


def extract_last_frame(clip: Clip, out_dir: Path, width: int = 1280) -> Frame:
    out_dir.mkdir(parents=True, exist_ok=True)
    out = out_dir / f"{clip.camera}.jpg"
    subprocess.run(
        ["ffmpeg", "-loglevel", "error", "-y", "-sseof", "-1", "-i", str(clip.path),
         "-frames:v", "1", "-vf", f"scale={width}:-2", "-q:v", "3", str(out)],
        check=True,
    )
    return Frame(camera=clip.camera, path=out, timestamp=clip.timestamp)


def frames_from_path(path: Path, out_dir: Path, max_age: timedelta | None = None) -> list[Frame]:
    """Accepts a TeslaCam folder (clips) or a folder of still images named <camera>.jpg/png."""
    images = [p for p in path.glob("*") if p.suffix.lower() in (".jpg", ".jpeg", ".png")] if path.is_dir() else []
    if images and not any(path.rglob("*.mp4")):
        return [Frame(camera=p.stem, path=p, timestamp=datetime.fromtimestamp(p.stat().st_mtime)) for p in sorted(images)]
    latest = latest_per_camera(find_clips(path), max_age=max_age)
    return [extract_last_frame(c, out_dir) for c in latest.values()]
