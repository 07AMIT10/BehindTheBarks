"""The user-editable pet profile (T1.8): one JSON file next to the event log.

The dashboard's PUT /profile saves here; every reader of `cfg["web"]["profile"]` (Runtime.status,
notification captions, the dashboard header) sees the saved values because create_app merges this
file over the config at startup and the route updates the cfg dict on each save.

Precedence: saved file > BTB_DOG_NAME/BTB_LOCATION env > YAML > built-in defaults.
The file lives on the host's disk: on Render's ephemeral free tier a redeploy loses it and the
profile falls back to the config — that is a fallback, never an error.
"""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from typing import Any, Mapping

log = logging.getLogger("profile-store")

FILENAME = "profile.json"
# The fields a viewer may set, with their max length: the header and captions render them inline.
LIMITS: dict[str, int] = {"dog_name": 64, "location": 64, "zone_label": 40}


def validate_profile(raw: Mapping[str, Any]) -> dict[str, str]:
    """The whitelisted, trimmed string fields of `raw`. Raises ValueError on a wrong type or length.

    Unknown keys are dropped rather than rejected: a newer dashboard cannot poison an older server.
    """
    out: dict[str, str] = {}
    for key, limit in LIMITS.items():
        if key not in raw:
            continue
        value = raw[key]
        if value is None:
            raise ValueError(f"{key} must be a string, not null")
        if not isinstance(value, str):
            raise ValueError(f"{key} must be a string, got {type(value).__name__}")
        value = value.strip()
        if len(value) > limit:
            raise ValueError(f"{key} is longer than {limit} characters")
        out[key] = value
    return out


class ProfileStore:
    """Reads/writes `profile.json` under the event log directory. Never raises on a bad file."""

    def __init__(self, log_dir: str | Path, defaults: Mapping[str, str] | None = None) -> None:
        self.dir = Path(log_dir)
        self.path = self.dir / FILENAME
        self.defaults = {k: str(v) for k, v in (defaults or {}).items() if k in LIMITS}

    def load(self) -> dict[str, str]:
        """The saved profile merged over the defaults; a missing or corrupt file reads as defaults."""
        merged = dict(self.defaults)
        try:
            saved = json.loads(self.path.read_text())
        except FileNotFoundError:
            return merged
        except (OSError, ValueError):
            log.warning("profile: %s is unreadable; using the configured defaults", self.path)
            return merged
        if not isinstance(saved, dict):
            log.warning("profile: %s is not a JSON object; using the configured defaults", self.path)
            return merged
        for key in LIMITS:
            value = saved.get(key)
            if isinstance(value, str):
                merged[key] = value
        return merged

    def save(self, profile: Mapping[str, Any]) -> dict[str, str]:
        """Validate, merge over the saved values, write atomically (tmp + os.replace).

        Merging is what makes a partial PUT (only `dog_name`) keep the other fields."""
        clean = validate_profile(profile)
        self.dir.mkdir(parents=True, exist_ok=True)
        merged = {**self.load(), **clean}
        tmp = self.path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(merged, indent=2))
        os.replace(tmp, self.path)  # atomic on POSIX and Windows
        return merged
