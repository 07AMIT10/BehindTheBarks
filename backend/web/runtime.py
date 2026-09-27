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
from typing import Any

from backend.contracts import AudioEvent, EmotionState, Features, FrameEvent, RulesLabel
from backend.fusion.llm_triggers import TriggerPolicy
from backend.fusion.state import FusionState, StateConfig
from backend.web.hub import Hub
from backend.web.settings import is_demo

log = logging.getLogger("runtime")


class Runtime:
    def __init__(self, cfg: dict, pipeline: Any, interpreter: Any, notifier: Any, hub: Hub, clock=time.time):
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

    # -- lifecycle ----------------------------------------------------------------------------
    def bind_loop(self, loop: asyncio.AbstractEventLoop) -> None:
        self._loop, self._loop_thread = loop, threading.get_ident()

    async def start(self) -> None:
        self.bind_loop(asyncio.get_running_loop())
        self._tasks = [
            asyncio.create_task(self._run_pipeline(), name="pipeline"),
            asyncio.create_task(self._tick_loop(), name="tick"),
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
    def status(self) -> dict:
        try:
            ps = self.pipeline.status()
        except Exception:  # noqa: BLE001 - includes NotImplementedError on the stub
            ps = None
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
        }

    def timeline(self) -> list[dict]:
        return [asdict(s) for s in self.state.timeline()]
