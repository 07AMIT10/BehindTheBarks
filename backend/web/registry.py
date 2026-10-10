"""The household registry (T2.2): one Runtime + Hub + pipeline per household, `legacy` first.

The `legacy` household wraps the objects injected into `create_app`, so `app.state.rt` and every
existing test keep working untouched. Any other household id lazily gets a deep-copied cfg (its own
`web.log_dir/<hid>`), its own Hub and Runtime, and is evicted after `web.registry.idle_evict_s` of
having neither a station nor a viewer.

Docker runs a single uvicorn worker, so an in-memory registry is the whole story; do not add workers.
"""

from __future__ import annotations

import copy
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Awaitable, Callable

from backend.notify.dashboard import DashboardOnlyNotifier
from backend.web.event_log import EventLog
from backend.web.hub import Hub
from backend.web.runtime import Runtime

LEGACY = "legacy"


class DisabledInterpreter:
    """A tester household's interpreter until its owner opts in (T2.9): no LLM, no API spend, no
    room images leaving the host. Runtime only reads these two attributes when it is off."""

    enabled = False
    last_call = None


@dataclass
class HouseholdRuntime:
    """Everything one household owns. `runtime.cfg` is that household's own deep copy."""

    household_id: str
    runtime: Runtime
    hub: Hub
    pipeline: Any
    used_at: float = 0.0
    stations: int = 0  # live station sockets; eviction needs zero of these and zero viewers
    viewers: int = 0


@dataclass
class HouseholdRegistry:
    cfg: dict
    make_pipeline: Callable[[dict], Any] | None = None
    make_interpreter: Callable[[dict], Any] | None = None
    make_notifier: Callable[[dict], Any] | None = None
    clock: Callable[[], float] = time.time
    idle_evict_s: float = 600.0
    sessionmaker: Any = None  # set by create_app: the household profile lives in the DB (T2.3)
    _households: dict[str, HouseholdRuntime] = field(default_factory=dict)

    # -- legacy ---------------------------------------------------------------------------------
    def use_legacy(self, runtime: Runtime, hub: Hub, pipeline: Any) -> HouseholdRuntime:
        """Wrap the objects create_app already built: the single-household behaviour, unchanged."""
        lr = HouseholdRuntime(household_id=LEGACY, runtime=runtime, hub=hub, pipeline=pipeline,
                              used_at=self.clock())
        self._households[LEGACY] = lr
        return lr

    # -- lookup ---------------------------------------------------------------------------------
    def peek(self, hid: str) -> HouseholdRuntime | None:
        return self._households.get(hid)

    async def get(self, hid: str) -> HouseholdRuntime:
        """The household's runtime, created on first use. `async` because Runtime.start() needs the loop."""
        found = self._households.get(hid)
        if found is not None:
            found.used_at = self.clock()
            return found
        lr = await self._create(hid)
        self._households[hid] = lr
        return lr

    async def _create(self, hid: str) -> HouseholdRuntime:
        cfg = copy.deepcopy(self.cfg)
        log_dir = str(Path(str(cfg["web"]["log_dir"])) / hid)
        cfg["web"]["log_dir"] = log_dir
        profile, log_enabled, llm_enabled = self._household_settings(hid, cfg)
        cfg["web"]["profile"] = profile
        hub = Hub(client_queue=cfg["web"]["ws"]["client_queue"], history=cfg["web"]["ws"]["history"],
                  frame_fps=cfg["web"]["ws"]["frame_fps"], clock=self.clock,
                  log=EventLog(log_dir) if log_enabled else None)
        pipeline = self.make_pipeline(cfg) if self.make_pipeline else None
        if pipeline is None:  # pragma: no cover - only when create_app injects nothing
            from backend.main import build_pipeline

            pipeline = build_pipeline(cfg)
        # Tester households must never inherit the operator's LLM keys or Telegram/WhatsApp tokens
        # (T2.9): a disabled interpreter and a dashboard-only notifier unless the owner opted in
        # (or the caller injected factories on purpose for the legacy household).
        if self.make_interpreter is not None:
            interpreter = self.make_interpreter(cfg)
        elif llm_enabled and cfg["web"].get("llm", {}).get("enabled"):
            from backend.fusion.llm_interpreter import LLMInterpreter
            from backend.web.settings import LLMSettings

            interpreter = LLMInterpreter(LLMSettings.from_config(cfg))
        else:
            interpreter = DisabledInterpreter()
        notifier = self.make_notifier(cfg) if self.make_notifier else DashboardOnlyNotifier()
        rt = Runtime(cfg, pipeline, interpreter, notifier, hub, clock=self.clock)
        await rt.start()
        return HouseholdRuntime(household_id=hid, runtime=rt, hub=hub, pipeline=pipeline,
                                used_at=self.clock())

    def _household_settings(self, hid: str, cfg: dict) -> tuple[dict, bool, bool]:
        """The profile, log flag and LLM opt-in from the DB row (T2.9); else the cfg."""
        if self.sessionmaker is None or hid == LEGACY:
            return (dict(cfg["web"].get("profile") or {}), bool(cfg["web"].get("log_enabled", True)),
                    bool(cfg["web"].get("llm", {}).get("enabled", False)))
        from backend.store import repo  # T2.3 owns the DB household; absent in the refactor-only tests

        try:
            with self.sessionmaker() as s:
                h = repo.get_household(s, hid)
                if h is None:
                    return dict(cfg["web"].get("profile") or {}), False, False
                return ({"dog_name": h.dog_name, "location": h.location,
                         "zone_label": cfg["web"]["profile"].get("zone_label", "feeding area")},
                        bool(h.log_enabled), bool(h.llm_enabled))
        except Exception:  # noqa: BLE001 - a missing table in pin mode must not break get()
            return dict(cfg["web"].get("profile") or {}), False, False

    # -- lifecycle ------------------------------------------------------------------------------
    async def sweep(self, now: float | None = None) -> dict[str, int | list[str]]:
        """Retention pass (T2.10): old session logs, expired pairing codes, dead viewers, idle
        runtimes. Every age is computed against the injected clock, so tests are exact.

        Only `session-*.jsonl` inside a household directory is ever deleted: a household's logs are
        its own, and nothing else in the log root is touched.
        """
        now = now if now is not None else self.clock()
        stats: dict[str, int | list[str]] = {
            "logs_deleted": 0, "pairing_codes_deleted": 0, "viewers_revoked": 0,
            "runtimes_evicted": [],
        }
        stats["logs_deleted"] = self._sweep_logs(now)
        if self.sessionmaker is not None:
            try:
                from backend.store import repo

                with self.sessionmaker() as s:
                    stats["pairing_codes_deleted"] = repo.expire_pairing_codes(s, 0.0, now=now)
                    stats["viewers_revoked"] = repo.revoke_expired_viewers(s, now=now)
                    s.commit()
            except Exception:  # noqa: BLE001 - retention must never take the server down
                pass
        stats["runtimes_evicted"] = await self.evict_idle(now=now)
        return stats

    def _sweep_logs(self, now: float) -> int:
        """Delete `<log_dir>/<hid>/session-*.jsonl` older than log_retention_days (default 7)."""
        cfg = self.cfg.get("web", {}) if isinstance(self.cfg, dict) else {}
        days = float(cfg.get("log_retention_days", 7) or 7)
        root = Path(str(cfg.get("log_dir", "out")))
        cutoff = now - days * 86_400.0
        deleted = 0
        try:
            if not root.is_dir():
                return 0
            # Only household subdirectories, and only files literally named session-*.jsonl.
            for household_dir in root.iterdir():
                if not household_dir.is_dir():
                    continue
                for path in household_dir.glob("session-*.jsonl"):
                    try:
                        if path.stat().st_mtime < cutoff:
                            path.unlink()
                            deleted += 1
                    except OSError:
                        continue
        except OSError:
            return deleted
        return deleted

    async def evict_idle(self, now: float | None = None) -> list[str]:
        """Stop and forget households with no station and no viewer for longer than idle_evict_s."""
        now = now if now is not None else self.clock()
        evicted: list[str] = []
        for hid, lr in list(self._households.items()):
            if hid == LEGACY:
                continue
            busy = lr.stations > 0 or lr.viewers > 0 or lr.runtime.phone is not None
            if busy or now - lr.used_at <= self.idle_evict_s:
                continue
            await lr.runtime.stop()
            lr.hub.close()
            del self._households[hid]
            evicted.append(hid)
        return evicted

    async def close(self) -> None:
        """Shutdown: stop and forget every non-legacy household. The legacy wrapper stays registered
        so post-shutdown reads of app.state* keep answering the way they did before the registry."""
        for hid, lr in list(self._households.items()):
            if hid != LEGACY:
                await lr.runtime.stop()
                lr.hub.close()
                del self._households[hid]

    async def forget(self, hid: str) -> bool:
        """Stop and remove one household now (deleted by its owner), legacy included-safe no-op."""
        lr = self._households.pop(hid, None)
        if lr is None:
            return False
        await lr.runtime.stop()
        lr.hub.close()
        return True
