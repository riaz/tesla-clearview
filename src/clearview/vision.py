"""Use Claude vision to judge fog/frost and lens obstruction for each camera frame."""

from __future__ import annotations

import base64
import json
import logging
from enum import Enum

import anthropic
from pydantic import BaseModel

from .cameras import WINDSCREEN_CAMERAS, Frame

log = logging.getLogger(__name__)


class Severity(str, Enum):
    none = "none"
    light = "light"
    moderate = "moderate"
    heavy = "heavy"


class Obstruction(str, Enum):
    none = "none"
    water_droplets = "water_droplets"
    condensation = "condensation"
    frost_ice = "frost_ice"
    snow = "snow"
    dirt_mud = "dirt_mud"
    glare = "glare"
    darkness = "darkness"
    other = "other"


class CameraReport(BaseModel):
    camera: str
    obstruction_pct: int
    obstruction_type: Obstruction
    glass_fog: Severity
    needs_cleaning: bool
    note: str


class VisionReport(BaseModel):
    cameras: list[CameraReport]
    windscreen_fog: Severity
    summary: str

    def dirty_cameras(self, threshold: int = 30) -> list[CameraReport]:
        return [c for c in self.cameras if c.needs_cleaning or c.obstruction_pct >= threshold]


SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["cameras", "windscreen_fog", "summary"],
    "properties": {
        "cameras": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["camera", "obstruction_pct", "obstruction_type", "glass_fog", "needs_cleaning", "note"],
                "properties": {
                    "camera": {"type": "string"},
                    "obstruction_pct": {"type": "integer", "description": "0-100: share of the view that is blocked or blurred"},
                    "obstruction_type": {"type": "string", "enum": [o.value for o in Obstruction]},
                    "glass_fog": {"type": "string", "enum": [s.value for s in Severity]},
                    "needs_cleaning": {"type": "boolean"},
                    "note": {"type": "string"},
                },
            },
        },
        "windscreen_fog": {"type": "string", "enum": [s.value for s in Severity]},
        "summary": {"type": "string"},
    },
}

SYSTEM = f"""You inspect still frames from a parked Tesla's cameras before a drive.
For each frame, judge:
- obstruction_pct: share of the field of view that is blocked, smeared or blurred by something on the lens or glass (0 means clear).
- obstruction_type: the main cause. Use "darkness" or "glare" when lighting is the problem rather than the lens. Night frames are not dirty just because they are dark.
- glass_fog: condensation or frost on the glass or lens in front of the camera.
- needs_cleaning: true only when the driver should physically wipe this camera (dirt, mud, salt, snow, ice, bird droppings). Do not set it for fog that the defroster will clear, or for lighting.
Cameras that look through the windscreen: {", ".join(sorted(WINDSCREEN_CAMERAS))}. Base windscreen_fog on those frames, plus any clear signs of misting or frost on glass in the other frames.
Be conservative. Do not report a problem you cannot see. Keep each note under 15 words."""


def _image_block(frame: Frame) -> dict:
    data = base64.standard_b64encode(frame.path.read_bytes()).decode()
    media = "image/png" if frame.path.suffix.lower() == ".png" else "image/jpeg"
    return {"type": "image", "source": {"type": "base64", "media_type": media, "data": data}}


def analyze(frames: list[Frame], model: str = "claude-opus-5-5", client: anthropic.Anthropic | None = None) -> VisionReport:
    if not frames:
        raise ValueError("no frames to analyze")
    client = client or anthropic.Anthropic()
    content: list[dict] = []
    for f in frames:
        content.append({"type": "text", "text": f"Camera: {f.camera} ({f.label}), captured {f.timestamp:%Y-%m-%d %H:%M}"})
        content.append(_image_block(f))
    content.append({"type": "text", "text": "Assess every camera above."})

    response = client.beta.messages.create(
        model=model,
        max_tokens=16000,
        system=SYSTEM,
        thinking={"type": "adaptive"},
        output_config={"effort": "low", "format": {"type": "json_schema", "schema": SCHEMA}},
        betas=["server-side-fallback-2026-07-01"],
        fallbacks="default",
        messages=[{"role": "user", "content": content}],
    )
    if response.stop_reason == "refusal":
        raise RuntimeError("Vision request was declined by the model")
    if response.stop_reason == "max_tokens":
        raise RuntimeError("Vision response was cut off at max_tokens")
    text = next(b.text for b in response.content if b.type == "text")
    report = VisionReport.model_validate(json.loads(text))
    for c in report.cameras:
        c.obstruction_pct = max(0, min(100, c.obstruction_pct))
    return report
