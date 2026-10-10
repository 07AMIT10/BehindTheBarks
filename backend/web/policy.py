"""Phase 3 policy engine (T3.3): per-household viewing mode policy.

The effective exposure for any viewer is:
    effective = min(station_cap, household_mode, viewer.mode_ceiling)

Where:
- station_cap is what the device holding the camera allows (reported via mode_state).
- household_mode is what the household owner selected (via /mode).
- viewer.mode_ceiling is the maximum exposure this viewer was granted when paired.
"""

from __future__ import annotations

import io
from typing import Any

from backend.web.modes import DEFAULT_PREVIEW, ModeError, min_mode, mode_rank, parse_mode

_EVENTS_ONLY_SLATE: bytes | None = None
_PRIVACY_SLATE: bytes | None = None


def build_events_only_slate() -> bytes:
    """Server-rendered slate for `privacy` mode: barks and telemetry flow, optical room video disabled."""
    global _EVENTS_ONLY_SLATE
    if _EVENTS_ONLY_SLATE is not None:
        return _EVENTS_ONLY_SLATE
    try:
        from PIL import Image, ImageDraw
        img = Image.new("RGB", (640, 360), color=(22, 24, 30))
        d = ImageDraw.Draw(img)
        d.rectangle([10, 10, 629, 349], outline=(55, 65, 81), width=2)
        d.text((250, 150), "EVENTS ONLY", fill=(239, 68, 68))
        d.text((160, 185), "Camera video disabled · Telemetry and barks only", fill=(156, 163, 175))
        b = io.BytesIO()
        img.save(b, format="JPEG", quality=80)
        _EVENTS_ONLY_SLATE = b.getvalue()
        return _EVENTS_ONLY_SLATE
    except Exception:
        _EVENTS_ONLY_SLATE = (
            b"\xff\xd8\xff\xe0\x00\x10JFIF\x00\x01\x01\x01\x00`\x00`\x00\x00"
            b"\xff\xdb\x00C\x00\x08\x06\x06\x07\x06\x05\x08\x07\x07\x07\t\t\x08\n\x0c"
            b"\x14\r\x0c\x0b\x0b\x0c\x19\x12\x13\x0f\x14\x1d\x1a\x1f\x1e\x1d\x1a\x1c"
            b"\x1c $.' \",#\x1c\x1c(7),01444\x1f'9=82<.342\xff\xc0\x00\x0b\x08\x00\x01"
            b"\x00\x01\x01\x01\x11\x00\xff\xc4\x00\x1f\x00\x00\x01\x05\x01\x01\x01\x01"
            b"\x01\x01\x00\x00\x00\x00\x00\x00\x00\x00\x01\x02\x03\x04\x05\x06\x07\x08"
            b"\t\n\x0b\xff\xda\x00\x08\x01\x01\x00\x00?\x00\xbf\x00\xff\xd9"
        )
        return _EVENTS_ONLY_SLATE


def build_privacy_slate() -> bytes:
    """Server-rendered slate for `blackout` mode (total mute of video and sound)."""
    from backend.web.remote_pipeline import _build_privacy_slate

    return _build_privacy_slate()


class Policy:
    """Per-household viewing mode policy (T3.3).

    Attributes:
        household_mode: Current owner-selected mode ("blackout", "privacy", "limited", "full").
        preview: Target preview parameters for the phone.
        viewers: Active viewer counts {"video": int, "audio": int}.
    """

    def __init__(self, household_mode: str = "privacy", preview: dict | None = None,
                 viewers: dict | None = None) -> None:
        self.household_mode = parse_mode(household_mode)
        self.preview = dict(preview or DEFAULT_PREVIEW)
        self.viewers = dict(viewers or {"video": 0, "audio": 0})
        self._prev_household_mode: str = "full" if self.household_mode == "blackout" else self.household_mode

    def compute_effective(self, station_cap: str = "full", viewer: Any = None, *, is_peeking: bool = False) -> str:
        """Compute effective exposure for a viewer: min(station_cap, household_mode, viewer.ceiling)."""
        cap = parse_mode(station_cap)
        ceiling = "full"
        if isinstance(viewer, str):
            ceiling = parse_mode(viewer)
        elif viewer is not None:
            c = getattr(viewer, "mode_ceiling", None)
            if c:
                ceiling = parse_mode(c)
        base_mode = "full" if is_peeking else self.household_mode
        return min_mode(cap, base_mode, ceiling)

    def mode_info(self, station_cap: str = "full", viewer: Any = None) -> dict[str, str]:
        cap = parse_mode(station_cap)
        return {
            "household_mode": self.household_mode,
            "station_cap": cap,
            "effective": self.compute_effective(cap, viewer),
        }

    def set_mode(self, mode: str) -> str:
        parsed = parse_mode(mode)
        if self.household_mode != "blackout":
            self._prev_household_mode = self.household_mode
        self.household_mode = parsed
        return parsed

    def restore_mode(self) -> str:
        mode = self._prev_household_mode if self._prev_household_mode != "blackout" else "full"
        return parse_mode(mode)
