"""Live-tunable bandwidth knobs. config.yaml gives the defaults; an admin PATCH stores overrides in the
database (app_settings), so they survive restarts and follow the app to another host. Nothing here
needs a redeploy.

Only the keys in TUNABLE can be changed. Each one is validated before anything is applied, so a bad
PATCH changes nothing. Only the overrides are stored: a value you never overrode still follows
config.yaml when the config changes.
"""

from __future__ import annotations

import json
import logging
import threading
import time
from pathlib import Path
from typing import Any, Protocol

from backend.store.models import AppSetting

log = logging.getLogger(__name__)

# key -> (type, min, max). Bools have min/max None.
TUNABLE: dict[str, tuple[type, float | None, float | None]] = {
    "video_fps": (float, 0.5, 30.0),        # MJPEG /video send rate ceiling (new frames only)
    "ws_frame_fps": (float, 0.5, 30.0),     # max frame envelopes/s per dashboard WebSocket
    "gzip_enabled": (bool, None, None),     # gzip JSON/JS/CSS/HTML responses (media and streams are never gzipped)
    "gzip_min_size": (int, 0, 1_000_000),   # bytes below which responses are not compressed
}

SETTING_KEY = "admin.tuning"


class TuningError(ValueError):
    """A PATCH body that breaks the rules; the message names the offending key."""


class TuningStore(Protocol):
    def load(self) -> dict[str, Any] | None:
        """The stored overrides, or None when nothing is stored."""

    def save(self, overrides: dict[str, Any]) -> None:
        """Replace the stored overrides. Raises if it cannot be saved."""


class DbTuningStore:
    """Overrides in the app_settings table, so every instance on the same database sees them."""

    def __init__(self, session_factory: Any) -> None:
        self._session = session_factory

    def load(self) -> dict[str, Any] | None:
        with self._session() as s:
            row = s.get(AppSetting, SETTING_KEY)
            return json.loads(row.value) if row is not None else None

    def save(self, overrides: dict[str, Any]) -> None:
        payload = json.dumps(overrides, sort_keys=True)
        with self._session() as s:
            row = s.get(AppSetting, SETTING_KEY)
            if row is None:
                s.add(AppSetting(key=SETTING_KEY, value=payload, updated_at=time.time()))
            else:
                row.value = payload
                row.updated_at = time.time()
            s.commit()


def _coerce(key: str, value: Any) -> Any:
    typ, lo, hi = TUNABLE[key]
    if typ is bool:
        if not isinstance(value, bool):
            raise TuningError(f"{key} must be true or false")
        return value
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise TuningError(f"{key} must be a number")
    num = typ(value)
    if lo is not None and num < lo:
        raise TuningError(f"{key} must be >= {lo}")
    if hi is not None and num > hi:
        raise TuningError(f"{key} must be <= {hi}")
    return num


class Tuning:
    """Process-wide view of the tunable values. Reads are lock-free dict lookups; writes are rare."""

    def __init__(self, defaults: dict[str, Any]) -> None:
        unknown = set(defaults) - set(TUNABLE)
        if unknown:
            raise TuningError(f"not tunable: {sorted(unknown)}")
        # Config defaults are only type-coerced: the range rules guard admin PATCHes, not the operator's file.
        self._defaults: dict[str, Any] = {k: TUNABLE[k][0](v) for k, v in defaults.items()}
        self._overrides: dict[str, Any] = {}
        self._values: dict[str, Any] = dict(self._defaults)
        self._store: TuningStore | None = None
        self._lock = threading.Lock()

    def attach(self, store: TuningStore, legacy_path: Path | None = None) -> None:
        """Connect the store and load the saved overrides. A pre-database tuning.json is imported once."""
        self._store = store
        stored = store.load()
        if stored is None and legacy_path is not None and legacy_path.exists():
            try:
                imported = self._validated(json.loads(legacy_path.read_text()))
            except (OSError, ValueError, TuningError) as exc:
                log.warning("not importing %s: %s", legacy_path, exc)
            else:
                store.save(imported)
                log.info("imported admin tuning from %s into the database", legacy_path)
                stored = imported
        if stored is not None:
            self._apply(stored)

    def refresh(self) -> None:
        """Re-read the overrides, so a change made on another instance reaches this one. A failed read keeps
        the current values rather than falling back to defaults."""
        if self._store is None:
            return
        try:
            stored = self._store.load() or {}
        except Exception as exc:  # noqa: BLE001 - the database may be briefly unreachable
            log.warning("tuning refresh skipped: %s", exc)
            return
        self._apply(stored)

    def _apply(self, overrides: dict[str, Any]) -> None:
        try:
            clean = self._validated(overrides)
        except TuningError as exc:
            log.warning("ignoring stored tuning overrides: %s", exc)
            return
        with self._lock:
            self._overrides = clean
            self._values = {**self._defaults, **clean}

    def _validated(self, patch: dict[str, Any]) -> dict[str, Any]:
        if not isinstance(patch, dict):
            raise TuningError("body must be a JSON object")
        bad = sorted(set(patch) - set(TUNABLE))
        if bad:
            raise TuningError(f"not tunable: {bad}")
        return {k: _coerce(k, v) for k, v in patch.items()}

    def get(self, key: str) -> Any:
        return self._values[key]

    def snapshot(self) -> dict[str, Any]:
        return dict(self._values)

    def update(self, patch: dict[str, Any]) -> dict[str, Any]:
        """Validate the whole patch, persist it, then apply. If the save fails nothing changes."""
        clean = self._validated(patch)
        with self._lock:
            overrides = {**self._overrides, **clean}
            if self._store is not None:
                self._store.save(overrides)
            self._overrides = overrides
            self._values = {**self._defaults, **overrides}
            return dict(self._values)
