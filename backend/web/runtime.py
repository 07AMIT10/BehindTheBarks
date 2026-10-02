"""Runtime: the only place that wires Pipeline -> FusionState / TriggerPolicy / LLM / Notifier -> Hub.

Pipeline callbacks may arrive on the loop thread (the contract) or, defensively, from worker threads;
off-thread calls are re-dispatched onto the loop. Nothing here blocks: LLM and notifier calls run as
tasks, and a failing task is logged, never propagated into the tick loop.
"""

from __future__ import annotations

import asyncio
import base64
import logging
import threading
import time
from collections import deque
from dataclasses import asdict
from typing import Any, Awaitable, Callable

from backend.contracts import AudioEvent, EmotionState, Features, FrameEvent, RulesLabel
from backend.demo.clips import DemoClips
from backend.fusion.llm_triggers import TriggerPolicy
from backend.fusion.state import FusionState, StateConfig
from backend.web.hub import Hub
from backend.web.ingest import Hello, IngestSession
from backend.web.ingest_events import RemoteHello, RemoteSession
from backend.web.settings import is_demo

log = logging.getLogger("runtime")


class Runtime:
    def __init__(self, cfg: dict, pipeline: Any, interpreter: Any, notifier: Any, hub: Hub, clock=time.time, demo_clips: DemoClips | None = None):
        self.cfg, self.pipeline, self.interpreter, self.notifier, self.hub, self.clock = (
            cfg, pipeline, interpreter, notifier, hub, clock)
        self.state = FusionState(StateConfig.from_config(cfg))
        self.triggers = TriggerPolicy.from_config(cfg)
        llm = cfg["web"]["llm"]
        self._context_s, self._max_samples = float(llm["context_s"]), int(llm["max_feature_samples"])
        self._features: deque[tuple[float, Features]] = deque(maxlen=256)
        self._audio: deque[AudioEvent] = deque(maxlen=64)
        self._rules: RulesLabel | None = None
        self._frame_times: deque[float] = deque(maxlen=64)
        self._last_live = -1e18
        self._llm_task: asyncio.Task | None = None
        self.pending: set[asyncio.Task] = set()
        self._tasks: list[asyncio.Task] = []
        self._loop: asyncio.AbstractEventLoop | None = None
        self._loop_thread: int | None = None
        self._stopped = False
        ing = cfg["web"]["ingest"]
        self._stale_s, self._phone_every_s = float(ing["stale_s"]), float(ing["status_every_s"])
        self._fps_window_s, self._max_msg = float(ing["fps_window_s"]), int(ing["max_message_bytes"])
        self.phone: IngestSession | RemoteSession | None = None
        self._phone_prev: IngestSession | RemoteSession | None = None
        self._phone_closer: Callable[[], Awaitable[None]] | None = None
        self.demo = demo_clips
        self.mode: str = "demo" if is_demo(cfg) and demo_clips and demo_clips.available else "live"
        self.demo_notified: set[str] = set()

    # -- lifecycle ----------------------------------------------------------------------------
    def bind_loop(self, loop: asyncio.AbstractEventLoop) -> None:
        self._loop, self._loop_thread = loop, threading.get_ident()

    async def start(self) -> None:
        self.bind_loop(asyncio.get_running_loop())
        self._tasks = [
            asyncio.create_task(self._run_pipeline(), name="pipeline"),
            asyncio.create_task(self._tick_loop(), name="tick"),
            asyncio.create_task(self._phone_loop(), name="phone-status"),
        ]

    async def stop(self) -> None:
        if self._stopped:
            return
        self._stopped = True
        try:
            self.pipeline.stop()
        except Exception:  # noqa: BLE001
            log.exception("pipeline.stop failed")
        for t in [*self._tasks, *self.pending]:
            t.cancel()
        await asyncio.gather(*self._tasks, *self.pending, return_exceptions=True)

    async def _run_pipeline(self) -> None:
        try:
            await self.pipeline.run(self.on_frame, self.on_audio, self.on_rules)
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001 - dashboard keeps running on whatever state it has
            log.exception("pipeline.run crashed")
            self.hub.publish("status", {"pipeline": "crashed"})

    async def _tick_loop(self) -> None:
        period = 1.0 / float(self.state.cfg.tick_hz)
        while True:
            try:
                self.step(self.clock())
            except Exception:  # noqa: BLE001
                log.exception("tick failed")
            await asyncio.sleep(period)

    async def _phone_loop(self) -> None:
        while True:
            await asyncio.sleep(self._phone_every_s)
            if self.phone is not None:
                self.publish_phone()

    # -- callbacks ----------------------------------------------------------------------------
    def _off_loop(self, fn, arg) -> bool:
        if self._loop is not None and threading.get_ident() != self._loop_thread:
            self._loop.call_soon_threadsafe(fn, arg)
            return True
        return False

    def on_frame(self, ev: FrameEvent) -> None:
        if self._off_loop(self.on_frame, ev):
            return
        self._frame_times.append(ev.ts)
        self.state.on_frame(ev)
        if ev.dog_detected:
            self._features.append((ev.ts, ev.features))
        self.hub.publish("frame", ev)

    def on_audio(self, ev: AudioEvent) -> None:
        if self._off_loop(self.on_audio, ev):
            return
        self._audio.append(ev)
        self.triggers.note_audio(ev)
        self.hub.publish("audio", ev)

    def on_rules(self, r: RulesLabel) -> None:
        if self._off_loop(self.on_rules, r):
            return
        self._rules = r
        self.state.on_rules(r)
        self.triggers.note_rules(r)
        self.hub.publish("rules", r)

    # -- actions ------------------------------------------------------------------------------
    def treat(self, now: float | None = None) -> float:
        now = self.clock() if now is None else now
        try:
            self.pipeline.mark_treat(now)
        except NotImplementedError:
            log.warning("pipeline has no mark_treat yet")
        self.triggers.note_treat()
        self.hub.publish("treat", {"ts": now})
        return now

    def set_mode(self, mode: str) -> str:
        if mode not in ("live", "demo"):
            raise ValueError(f"mode must be live|demo, got {mode!r}")
        if mode == "demo" and (self.demo is None or not self.demo.available):
            raise ValueError("no demo clips available")
        self.mode = mode
        self.hub.publish("status", {"mode": mode, "modes": self._modes()})
        return mode

    def _modes(self) -> list[str]:
        return ["live", "demo"] if self.demo is not None and self.demo.available else ["live"]

    async def demo_notify(self, clip_id: str, t: float) -> dict:
        """First demo notification per clip: real Telegram only when telegram_first is on and the
        notifier is a Telegram one; otherwise a dashboard_only record. Always idempotent."""
        if clip_id in self.demo_notified:
            return {"status": "dashboard_only", "duplicate": True}
        self.demo_notified.add(clip_id)
        if self.cfg["web"]["demo"].get("telegram_first") and type(self.notifier).__name__ == "TelegramNotifier":
            jpeg = self._latest_jpeg()
            res = await self.notifier.send(
                EmotionState(ts=t, emotion="unknown", confidence=0.0, source="rules",
                             reason=f"Demo clip {clip_id} at {t:.1f}s."), jpeg)
            return {"status": res.status, "detail": res.detail, "duplicate": False}
        return {"status": "dashboard_only", "detail": "would send to owner", "duplicate": False}

    def step(self, now: float) -> None:
        t = self.state.tick(now)
        if t.changed is not None:
            self.hub.publish("emotion", t.changed, meta={"changed": True})
            self._last_live = now
        elif now - self._last_live >= 1.0 / float(self.state.cfg.live_hz) - 1e-9:
            self.hub.publish("emotion", t.current, meta={"changed": False})
            self._last_live = now
        if t.notify is not None:
            self._spawn(self._notify(t.notify))
        in_flight = self._llm_task is not None and not self._llm_task.done()
        reason = self.triggers.due(now, in_flight) if self.interpreter.enabled else None
        if reason:
            self._llm_task = self._spawn(self._run_llm(reason, now))

    # -- phone (/ingest, /ingest-events) ---------------------------------------------------------
    def phone_open(self, hello: Hello | RemoteHello, closer: Callable[[], Awaitable[None]] | None = None,
                   session: IngestSession | RemoteSession | None = None) -> IngestSession | RemoteSession | None:
        """Register a phone that sent a valid hello. Returns None if another phone is active (busy).

        A phone that has sent nothing for ingest.stale_s counts as gone (half-open socket after a
        Wi-Fi drop), so the newcomer replaces it and the old socket is closed via its closer.

        `session` is the protocol-specific session (/ingest-events builds a RemoteSession, which
        dispatches envelopes to this Runtime); without it a plain IngestSession is made."""
        now = self.clock()
        old = self.phone
        if old is not None:
            if old.idle_s(now) < self._stale_s:
                return None
            old.connected = False
            if self._phone_closer is not None:
                self._spawn(self._phone_closer())
            log.warning("phone %r went quiet for %.1fs; replaced", old.hello.device, old.idle_s(now))
        self.phone = session or IngestSession(hello, self.pipeline, clock=self.clock,
                                              fps_window_s=self._fps_window_s, max_message_bytes=self._max_msg)
        self._phone_closer = closer
        log.info("phone connected: %s", hello)
        self.publish_phone()
        return self.phone

    def phone_close(self, session: IngestSession | RemoteSession, code: int | None = None) -> None:
        """The phone's socket ended. Ignored if this session was already replaced.

        Close code 1000 means the Stop button: the phone is forgotten (status "phone": null). Anything
        else (page killed, Wi-Fi drop) keeps a disconnected snapshot so the dashboard can say so."""
        session.connected = False
        if self.phone is not session:
            return
        stopped = code == 1000
        self.phone, self._phone_closer = None, None
        self._phone_prev = None if stopped else session
        log.info("phone %s: %s (code %s)", "stopped" if stopped else "disconnected", session.hello.device, code)
        self.publish_phone()

    def phone_status(self) -> dict | None:
        s = self.phone or self._phone_prev
        return None if s is None else s.snapshot(self.clock())

    def publish_phone(self) -> None:
        self.hub.publish("status", {"phone": self.phone_status(), "pipeline_status": self._pipeline_status()})

    # -- tasks --------------------------------------------------------------------------------
    def _spawn(self, coro) -> asyncio.Task:
        task = asyncio.get_running_loop().create_task(coro)
        self.pending.add(task)

        def done(t: asyncio.Task) -> None:
            self.pending.discard(t)
            if not t.cancelled() and t.exception():
                log.error("background task failed", exc_info=t.exception())
        task.add_done_callback(done)
        return task

    def _context(self, now: float) -> tuple[list[tuple[float, Features]], list[AudioEvent]]:
        feats = [(ts, f) for ts, f in self._features if ts >= now - self._context_s]
        if len(feats) > self._max_samples:
            step = len(feats) / self._max_samples
            feats = [feats[int(i * step)] for i in range(self._max_samples - 1)] + [feats[-1]]
        audio = [a for a in self._audio if a.ts >= now - self._context_s]
        return feats, audio

    async def _run_llm(self, reason: str, now: float) -> None:
        feats, audio = self._context(now)
        jpeg = self._latest_jpeg()
        result = await self.interpreter.interpret(trigger=reason, now=now, features=feats, audio=audio,
                                                  rules=self._rules, jpeg=jpeg)
        if result is not None:
            self.state.on_llm(result)
            self.hub.publish("llm", result, meta={"ok": True})
        else:
            self.hub.publish("llm", dict(self.interpreter.last_call or {"trigger": reason}), meta={"ok": False})

    async def _notify(self, state: EmotionState) -> None:
        jpeg = self._latest_jpeg()
        snap = f"data:image/jpeg;base64,{base64.b64encode(jpeg).decode('ascii')}" if jpeg else None
        state = state.model_copy(update={"snapshot": snap})
        res = await self.notifier.send(state, jpeg)
        self.hub.publish("notification", {"state": state.model_dump(mode="json"), "status": res.status,
                                          "channel": res.channel, "detail": res.detail})

    def _latest_jpeg(self) -> bytes | None:
        try:
            return self.pipeline.latest_frame_jpeg()
        except Exception:  # noqa: BLE001
            return None

    # -- read models --------------------------------------------------------------------------
    def _pipeline_status(self) -> dict | None:
        try:
            return self.pipeline.status()
        except Exception:  # noqa: BLE001 - includes NotImplementedError on the stub
            return None

    def status(self) -> dict:
        ps = self._pipeline_status()
        ft = list(self._frame_times)
        fps = round((len(ft) - 1) / (ft[-1] - ft[0]), 1) if len(ft) > 1 and ft[-1] > ft[0] else 0.0
        s = getattr(self.interpreter, "s", None)
        return {
            "pipeline": self.cfg["web"]["pipeline"],
            "pipeline_status": ps,
            "demo_mode": is_demo(self.cfg),
            "llm": {"enabled": bool(self.interpreter.enabled),
                    "online": (self.interpreter.last_call or {}).get("outcome", "ok") == "ok",
                    "provider": getattr(s, "provider", None), "model": getattr(s, "model", None),
                    "vision": getattr(s, "vision", None), "last_call": self.interpreter.last_call},
            "notify_mode": self.cfg["web"]["notify"]["mode"],
            "clients": self.hub.client_count,
            "fps": fps,
            "profile": self.cfg["web"]["profile"],
            "phone": self.phone_status(),
            "modes": self._modes(),
            "mode": self.mode,
        }

    def timeline(self) -> list[dict]:
        return [asdict(s) for s in self.state.timeline()]
