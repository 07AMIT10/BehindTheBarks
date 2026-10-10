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
from backend.web.hub import HouseholdHub, Hub
from backend.web.limited import Limited
from backend.web.policy import Policy
from backend.web.runtime import Runtime
from backend.web.tracker import ViewerTracker

LEGACY = "legacy"


class DisabledInterpreter:
    """A tester household's interpreter until its owner opts in (T2.9): no LLM, no API spend, no
    room images leaving the host. Runtime only reads these two attributes when it is off."""

    enabled = False
    last_call = None


@dataclass
class StationChannel:
    """A per-station channel (T4.1): its own Runtime, Pipeline, Hub, Policy, Tracker, Limited."""

    station_id: str
    name: str
    runtime: Runtime
    hub: Hub
    pipeline: Any
    policy: Policy = field(default_factory=Policy)
    tracker: Any = None
    limited: Any = None
    used_at: float = 0.0


@dataclass
class HouseholdRuntime:
    """Everything one household owns. `runtime.cfg` is that household's own deep copy."""

    household_id: str
    runtime: Runtime
    hub: Hub
    pipeline: Any
    policy: Policy = field(default_factory=Policy)
    tracker: Any = None
    limited: Any = None
    used_at: float = 0.0
    stations: int = 0  # live station sockets; eviction needs zero of these and zero viewers
    viewers: int = 0
    channels: dict[str, StationChannel] = field(default_factory=dict)
    default_channel_id: str | None = None
    registry: Any = None

    def get_channel(self, station_id: str | None) -> StationChannel | None:
        if station_id:
            return self.channels.get(station_id)
        if self.default_channel_id and self.default_channel_id in self.channels:
            return self.channels[self.default_channel_id]
        if self.channels:
            return next(iter(self.channels.values()))
        return None

    async def channel(self, station_id: str, name: str = "") -> StationChannel:
        """Get or create StationChannel for station_id (T4.1)."""
        clock = self.registry.clock if self.registry else time.time
        now = clock()
        self.used_at = now
        ch = self.channels.get(station_id)
        if ch is not None:
            ch.used_at = now
            if name and not ch.name:
                ch.name = name
            return ch

        if "default" in self.channels and self.channels["default"].runtime.phone is None:
            old_ch = self.channels.pop("default")
            if self.default_channel_id == "default":
                self.default_channel_id = None
            if old_ch.runtime is not None:
                await old_ch.runtime.stop()

        max_stations = 4
        if self.registry and isinstance(self.registry.cfg, dict):
            max_stations = int(self.registry.cfg.get("web", {}).get("max_stations_per_household", 4) or 4)
        if len(self.channels) >= max_stations:
            raise ValueError(f"Station limit of {max_stations} reached for this household")

        cfg = copy.deepcopy(self.registry.cfg) if self.registry else copy.deepcopy(self.runtime.cfg)
        log_dir = str(Path(str(cfg["web"]["log_dir"])) / f"{self.household_id}_{station_id}")
        profile = dict(self.runtime.cfg["web"].get("profile") or {})
        if name:
            profile["name"] = name
        cfg["web"]["profile"] = profile

        st_hub = Hub(client_queue=cfg["web"]["ws"]["client_queue"],
                     history=cfg["web"]["ws"]["history"],
                     frame_fps=cfg["web"]["ws"]["frame_fps"],
                     clock=clock,
                     log=None)
        if isinstance(self.hub, HouseholdHub):
            st_hub.add_tap(lambda env, s_id=station_id: self.hub.on_station_envelope(s_id, env))
        else:
            st_hub.add_tap(lambda env, s_id=station_id: self.hub.publish(
                env.get("type", "frame"), env.get("data", {}), meta={**env.get("meta", {}), "station": s_id}))

        pipeline = self.registry.make_pipeline(cfg) if (self.registry and self.registry.make_pipeline) else None
        if pipeline is None:
            from backend.main import build_pipeline
            pipeline = build_pipeline(cfg)

        interpreter = DisabledInterpreter()
        notifier = DashboardOnlyNotifier()
        policy = Policy(household_mode=self.policy.household_mode)
        tracker = ViewerTracker(household_id=self.household_id, policy=policy, pipeline=pipeline, clock=clock)
        tracker.register_kick_callback(st_hub.kick_all_viewers)
        limited = Limited(clock=clock)

        rt = Runtime(cfg, pipeline, interpreter, notifier, st_hub, clock=clock, policy=policy)
        rt.policy = policy
        rt.tracker = tracker
        rt.limited = limited
        if hasattr(pipeline, "_policy_mode"):
            pipeline._policy_mode = policy.household_mode
        await rt.start()

        ch = StationChannel(station_id=station_id, name=name or f"Station {len(self.channels)+1}",
                            runtime=rt, hub=st_hub, pipeline=pipeline, policy=policy,
                            tracker=tracker, limited=limited, used_at=now)
        self.channels[station_id] = ch
        if self.default_channel_id is None:
            self.default_channel_id = station_id
            self.runtime = rt
            self.pipeline = pipeline
        return ch


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
        policy = getattr(runtime, "policy", None) or Policy(household_mode="full")
        runtime.policy = policy
        tracker = getattr(runtime, "tracker", None) or ViewerTracker(
            household_id=LEGACY, policy=policy, pipeline=pipeline, clock=self.clock
        )
        tracker.register_kick_callback(hub.kick_all_viewers)
        runtime.tracker = tracker
        limited = getattr(runtime, "limited", None) or Limited(clock=self.clock)
        runtime.limited = limited
        lr = HouseholdRuntime(household_id=LEGACY, runtime=runtime, hub=hub, pipeline=pipeline,
                              policy=policy, tracker=tracker, limited=limited, used_at=self.clock(),
                              registry=self)
        default_ch = StationChannel(station_id=LEGACY, name="Default", runtime=runtime,
                                    hub=hub, pipeline=pipeline, policy=policy, tracker=tracker,
                                    limited=limited, used_at=self.clock())
        lr.channels[LEGACY] = default_ch
        lr.default_channel_id = LEGACY
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
        hub = HouseholdHub(client_queue=cfg["web"]["ws"]["client_queue"], history=cfg["web"]["ws"]["history"],
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
        policy = Policy(household_mode="privacy")
        tracker = ViewerTracker(household_id=hid, policy=policy, pipeline=pipeline, clock=self.clock)
        tracker.register_kick_callback(hub.kick_all_viewers)
        limited = Limited(clock=self.clock)
        rt = Runtime(cfg, pipeline, interpreter, notifier, hub, clock=self.clock, policy=policy)
        rt.policy = policy
        rt.tracker = tracker
        rt.limited = limited
        if hasattr(pipeline, "_policy_mode"):
            pipeline._policy_mode = policy.household_mode
        await rt.start()
        lr = HouseholdRuntime(household_id=hid, runtime=rt, hub=hub, pipeline=pipeline,
                              policy=policy, tracker=tracker, limited=limited, used_at=self.clock(),
                              registry=self)
        default_ch = StationChannel(station_id="default", name="Default", runtime=rt,
                                    hub=hub, pipeline=pipeline, policy=policy, tracker=tracker,
                                    limited=limited, used_at=self.clock())
        lr.channels["default"] = default_ch
        lr.default_channel_id = "default"
        return lr

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
        """Stop and forget households with no station and no viewer for longer than idle_evict_s (T2.2, T4.1)."""
        now = now if now is not None else self.clock()
        evicted: list[str] = []
        for hid, lr in list(self._households.items()):
            if hid == LEGACY:
                continue
            # Evict station channels idle for > 600s with no phone and no viewers (T4.1 step 5)
            for sid, ch in list(lr.channels.items()):
                if ch.runtime.phone is None and ch.hub.client_count == 0 and now - ch.used_at > 600.0:
                    await ch.runtime.stop()
                    ch.hub.close()
                    del lr.channels[sid]
                    if lr.default_channel_id == sid:
                        lr.default_channel_id = next(iter(lr.channels.keys())) if lr.channels else None

            busy = lr.stations > 0 or lr.viewers > 0 or lr.runtime.phone is not None or any(ch.runtime.phone is not None for ch in lr.channels.values())
            if busy or now - lr.used_at <= self.idle_evict_s:
                continue
            for ch in list(lr.channels.values()):
                await ch.runtime.stop()
                ch.hub.close()
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
                for ch in list(lr.channels.values()):
                    await ch.runtime.stop()
                    ch.hub.close()
                await lr.runtime.stop()
                lr.hub.close()
                del self._households[hid]

    async def forget(self, hid: str) -> bool:
        """Stop and remove one household now (deleted by its owner), legacy included-safe no-op."""
        lr = self._households.pop(hid, None)
        if lr is None:
            return False
        for ch in list(lr.channels.values()):
            await ch.runtime.stop()
            ch.hub.close()
        await lr.runtime.stop()
        lr.hub.close()
        return True
