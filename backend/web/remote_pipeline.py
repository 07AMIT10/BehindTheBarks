"""RemotePipeline (`web.pipeline: remote`): the perception stack runs on the phone, so the server holds
no models, starts no watchdog and never ingests pixels itself. It is the Pipeline implementation the
event sink needs:

    run()               awaits until stop(): no source, no models, no watchdog thread
    ingest_frame()      the phone's 0x01 preview JPEG, so /video and notification snapshots work
    mark_treat()        pushes {"type": "treat"} back down, so the phone's rules see treat_event_recent
    status()            mirrors the phone's own metrics and reports stalled after web.remote.stale_s

Everything the pipeline would normally report as a fault is the phone's fault now: no previews or no
envelopes for longer than stale_s means the app (or the network) went quiet, and the Runtime's
no-dog-to-unknown path takes the label to unknown on its own — there is no watchdog to inject
synthetic frames.
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections import deque
from typing import Any, Awaitable, Callable

log = logging.getLogger("remote-pipeline")

DOWNLINK_QUEUE = 64


class RemotePipeline:
    def __init__(self, config: dict[str, Any], clock: Callable[[], float] = time.time) -> None:
        web = config.get("web") or {}
        remote = web.get("remote") or {}
        source = (config.get("data") or {}).get("source") or {}
        self.cfg = config
        self.clock = clock
        # Same meaning as the browser source's stall_s in Pipeline: no camera data for this long.
        self.stale_s = float(remote.get("stale_s", source.get("stall_s", 2.0)))
        self._started_at = clock()
        self._stopped = False
        self._running = False
        self._halt: asyncio.Event | None = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self._session: Any = None
        self._send: Callable[[dict], Awaitable[None]] | None = None
        self._jpeg: bytes | None = None
        self._preview_ts: float | None = None
        self._raw_audio_ts: float | None = None
        self._treats: deque[float] = deque(maxlen=32)
        self._queue: deque[dict] = deque(maxlen=DOWNLINK_QUEUE)
        self._flushing = False
        self.downlinks_sent = 0
        self.downlinks_dropped = 0

    # -- Pipeline interface -------------------------------------------------------------------
    async def run(self, on_frame_event=None, on_audio_event=None, on_rules_label=None) -> None:
        """Await forever. Events arrive through the /ingest-events socket, not from a source."""
        self._loop = asyncio.get_running_loop()
        self._halt = asyncio.Event()
        self._running = True
        try:
            await self._halt.wait()
        finally:
            self._running = False

    def stop(self) -> None:
        self._stopped, self._running = True, False
        if self._halt is not None:
            self._halt.set()

    def latest_frame_jpeg(self) -> bytes | None:
        """The newest preview JPEG the phone sent, or None before the first one."""
        return self._jpeg

    def mark_treat(self, ts: float) -> None:
        """A treat at `ts` (dashboard button, or the phone's own treat envelope): the phone's rules
        engine needs it too, so it goes down the same socket."""
        self._treats.append(float(ts))
        self._post({"type": "treat", "ts": float(ts)})

    def ingest_frame(self, jpeg: bytes, ts: float) -> None:
        if jpeg[:2] == b"\xff\xd8":  # JPEG SOI; anything else is ignored, never raised
            self._jpeg, self._preview_ts = bytes(jpeg), float(ts)

    def ingest_audio(self, pcm16: bytes, sample_rate: int, ts: float) -> None:
        """Not part of /ingest-events: the phone sends AudioEvents as JSON. Kept for the interface."""
        self._raw_audio_ts = float(ts)

    def push_config(self, rules: dict | None = None) -> bool:
        """Push data.rules overrides to the phone, so retuning needs no APK rebuild. With no
        `rules`, the server's own data.rules section is sent. False when no phone is attached."""
        sent = self._post({"type": "config", "data": {"rules": self._rules_config(rules)}})
        log.info("remote: pushed config to the phone (%s)", "sent" if sent else "no phone")
        return sent

    # -- phone session --------------------------------------------------------------------------
    def attach(self, session: Any, send: Callable[[dict], Awaitable[None]] | None) -> None:
        """The live phone session plus the downlink sender, wired up by the /ingest-events endpoint."""
        self._session, self._send = session, send
        device = getattr(getattr(session, "hello", None), "device", None)
        log.info("remote: phone attached: %s", device or "unknown")

    def detach(self, session: Any = None) -> None:
        """The socket is gone: stop feeding metrics from it and drop anything queued for it."""
        if session is None or self._session is session:
            self._session, self._send = None, None
            self._queue.clear()

    def _rules_config(self, rules: dict | None) -> dict:
        if rules is not None:
            return dict(rules)
        return dict(((self.cfg.get("data") or {}).get("rules") or {}))

    # -- downlinks ------------------------------------------------------------------------------
    def _post(self, msg: dict) -> bool:
        """Queue one downlink. Never blocks the caller and never raises: the queue drains in order on
        a task, and a socket that has gone away just loses the message."""
        if self._send is None or self._stopped:
            log.info("remote: %s downlink dropped, no phone attached", msg.get("type"))
            self.downlinks_dropped += 1
            return False
        if len(self._queue) == self._queue.maxlen:
            self.downlinks_dropped += 1  # a phone that cannot keep up loses the oldest downlink
        self._queue.append(msg)
        self._schedule_flush()
        return True

    def _schedule_flush(self) -> None:
        if self._flushing:
            return
        loop = self._loop
        if loop is None:
            try:
                loop = asyncio.get_running_loop()  # mark_treat() from a route, before run() started
            except RuntimeError:
                loop = None
        if loop is None or loop.is_closed():
            self.downlinks_dropped += len(self._queue)
            self._queue.clear()
            return
        self._flushing = True
        try:
            loop.create_task(self._flush(), name="remote-downlink")
        except RuntimeError:
            self._flushing = False
            self.downlinks_dropped += len(self._queue)
            self._queue.clear()

    async def _flush(self) -> None:
        try:
            while self._queue:
                msg = self._queue.popleft()
                try:
                    await self._send(msg)  # type: ignore[misc]
                except Exception:  # noqa: BLE001 - a dead socket must not stop the queue
                    log.warning("remote: downlink %s failed", msg.get("type"), exc_info=True)
                    self.downlinks_dropped += 1
                else:
                    self.downlinks_sent += 1
        finally:
            self._flushing = False

    # -- status ---------------------------------------------------------------------------------
    def status(self) -> dict:
        """The pipeline.status() shape, with the phone's metrics instead of our own counters."""
        now = self.clock()
        m = self._metrics(now)
        age = self._frame_age_s(now)
        return {
            "source": "remote",
            "state": self._state(now),
            "fps": m["events_s"],
            "last_frame_age_s": None if age is None else round(age, 3),
            "audio_ok": bool(m["audio_ok"]),
            "preview_fps": m["preview_fps"],
            "events": m["events"],
            "previews": m["previews"],
            "dropped": m["dropped"],
            "device": m["device"],
            "models": m["models"],
            "clock_offset_s": m["clock_offset_s"],
            "clock_samples": m["clock_samples"],
            "ping_due_s": m["ping_due_s"],
            "downlinks_sent": self.downlinks_sent,
            "downlinks_dropped": self.downlinks_dropped,
            "treats": len(self._treats),
        }

    def _metrics(self, now: float) -> dict:
        if self._session is not None and hasattr(self._session, "metrics"):
            return self._session.metrics(now)
        return {"events_s": 0.0, "preview_fps": 0.0, "events": 0, "previews": 0, "dropped": 0, "device": None,
                "models": {}, "clock_offset_s": 0.0, "clock_samples": 0, "ping_due_s": None, "audio_ok": False,
                "last_event_age_s": None, "last_frame_age_s": None, "last_preview_age_s": None}

    def _frame_ts(self) -> float | None:
        """The newest camera data we have seen: a FrameEvent envelope or a preview JPEG."""
        frame_ts = getattr(self._session, "last_frame_ts", None)
        if frame_ts is None:
            return self._preview_ts
        return max(frame_ts, self._preview_ts) if self._preview_ts is not None else frame_ts

    def _frame_age_s(self, now: float) -> float | None:
        ts = self._frame_ts()
        return None if ts is None else now - ts

    def _state(self, now: float) -> str:
        if self._stopped:
            return "stopped"
        age = self._frame_age_s(now)
        # Never having had a frame counts as stalled once the grace period is over (Pipeline._state).
        since = age if age is not None else now - self._started_at
        return "stalled" if since > self.stale_s else "running"
