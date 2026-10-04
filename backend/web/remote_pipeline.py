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
TORCH_RATE_LIMIT_S = 1.2
FLIP_RATE_LIMIT_S = 2.5
TREAT_RATE_LIMIT_S = 3.5


def _build_privacy_slate() -> bytes:
    try:
        import io
        from PIL import Image, ImageDraw
        img = Image.new("RGB", (640, 360), color=(22, 24, 30))
        d = ImageDraw.Draw(img)
        d.rectangle([10, 10, 629, 349], outline=(55, 65, 81), width=2)
        d.text((230, 160), "PRIVACY MODE ACTIVE", fill=(239, 68, 68))
        d.text((180, 190), "Live camera preview & microphone are muted", fill=(156, 163, 175))
        b = io.BytesIO()
        img.save(b, format="JPEG", quality=80)
        return b.getvalue()
    except Exception:
        return (
            b"\xff\xd8\xff\xe0\x00\x10JFIF\x00\x01\x01\x01\x00`\x00`\x00\x00"
            b"\xff\xdb\x00C\x00\x08\x06\x06\x07\x06\x05\x08\x07\x07\x07\t\t\x08\n\x0c"
            b"\x14\r\x0c\x0b\x0b\x0c\x19\x12\x13\x0f\x14\x1d\x1a\x1f\x1e\x1d\x1a\x1c"
            b"\x1c $.' \",#\x1c\x1c(7),01444\x1f'9=82<.342\xff\xc0\x00\x0b\x08\x00\x01"
            b"\x00\x01\x01\x01\x11\x00\xff\xc4\x00\x1f\x00\x00\x01\x05\x01\x01\x01\x01"
            b"\x01\x01\x00\x00\x00\x00\x00\x00\x00\x00\x01\x02\x03\x04\x05\x06\x07\x08"
            b"\t\n\x0b\xff\xda\x00\x08\x01\x01\x00\x00?\x00\xbf\x00\xff\xd9"
        )


class RemotePipeline:
    def __init__(self, config: dict[str, Any], clock: Callable[[], float] = time.time) -> None:
        web = config.get("web") or {}
        remote = web.get("remote") or {}
        source = (config.get("data") or {}).get("source") or {}
        self.cfg = config
        self.clock = clock
        # Same meaning as the browser source's stall_s in Pipeline: no camera data for this long.
        self.stale_s = float(remote.get("stale_s", source.get("stall_s", 2.0)))
        # A downlink on a half-open socket never resolves, so every send gets a deadline: without one
        # the flush task wedges and every later treat and config push piles up undelivered.
        self.send_timeout_s = float(remote.get("downlink_timeout_s", 5.0))
        self.flush_warn_s = float(remote.get("ping_every_s", 30.0))
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
        self._flush_since: float | None = None
        self._flush_warned_at: float | None = None
        self._flush_task: asyncio.Task | None = None
        self.downlinks_sent = 0
        self.downlinks_dropped = 0
        self._torch_enabled = False
        self._last_torch_ts = 0.0
        self._last_flip_ts = 0.0
        self._last_treat_ts = 0.0
        self._privacy_mode = False
        self._privacy_jpeg: bytes = _build_privacy_slate()

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
        if self._flush_task is not None:
            self._flush_task.cancel()
            self._flush_task = None

    def latest_frame_jpeg(self) -> bytes | None:
        """The newest preview JPEG the phone sent, or privacy slate in privacy mode, or None before the first one."""
        if self._privacy_mode:
            return self._privacy_jpeg
        return self._jpeg

    def mark_treat(self, ts: float) -> None:
        """A treat at `ts` (dashboard button, or the phone's own treat envelope): the phone's rules
        engine needs it too, so it goes down the same socket.

        `ts` is on the server clock (that is what Runtime.treat() holds), but the app compares the
        treat against its own clock, so the downlink carries the phone-clock equivalent plus the
        offset it was converted with."""
        self._treats.append(float(ts))
        offset = self._clock_offset()
        self._post({"type": "treat", "data": {"ts": float(ts) - offset, "offset_s": offset}})

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

    def set_torch(self, enabled: bool) -> bool:
        """Control the phone's camera flashlight / torch remotely."""
        self._torch_enabled = bool(enabled)
        sent = self._post({"type": "torch", "data": {"enabled": self._torch_enabled}})
        log.info("remote: sent torch=%s to phone (%s)", self._torch_enabled, "sent" if sent else "no phone")
        return sent

    def get_torch(self) -> bool:
        return self._torch_enabled

    def flip_camera(self) -> bool:
        """Flip the phone's camera between back and front lens remotely."""
        sent = self._post({"type": "flip", "data": {}})
        log.info("remote: sent camera flip to phone (%s)", "sent" if sent else "no phone")
        return sent

    def set_privacy(self, enabled: bool) -> bool:
        """Enable or disable family privacy mode remotely (mutes mic & replaces video with privacy slate)."""
        self._privacy_mode = bool(enabled)
        sent = self._post({"type": "privacy", "data": {"enabled": self._privacy_mode}})
        log.info("remote: sent privacy=%s to phone (%s)", self._privacy_mode, "sent" if sent else "no phone")
        return sent

    def get_privacy(self) -> bool:
        return self._privacy_mode

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

    def _clock_offset(self) -> float:
        """server - phone, from the live session; 0 with no phone (so treat_ts passes through)."""
        offset = getattr(getattr(self._session, "offset", None), "offset", None)
        return 0.0 if offset is None else float(offset)

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
        self._flushing, self._flush_since = True, self.clock()
        try:
            # keep a reference: a bare create_task() result can be garbage-collected mid-send
            self._flush_task = loop.create_task(self._flush(), name="remote-downlink")
        except RuntimeError:
            self._flushing, self._flush_since, self._flush_task = False, None, None
            self.downlinks_dropped += len(self._queue)
            self._queue.clear()

    async def _flush(self) -> None:
        try:
            while self._queue:
                send = self._send
                if send is None:  # the phone went away while this flush was in flight (detach clears it)
                    self.downlinks_dropped += len(self._queue)
                    self._queue.clear()
                    break
                msg = self._queue.popleft()
                try:
                    await asyncio.wait_for(send(msg), timeout=self.send_timeout_s)
                except asyncio.TimeoutError:
                    log.warning("remote: downlink %s timed out after %.1fs", msg.get("type"), self.send_timeout_s)
                    self.downlinks_dropped += 1
                except Exception as err:  # noqa: BLE001 - a dead socket must not stop the queue
                    log.warning("remote: downlink %s failed (%s)", msg.get("type"), type(err).__name__)
                    self.downlinks_dropped += 1
                else:
                    self.downlinks_sent += 1
        finally:
            self._flushing, self._flush_since, self._flush_task = False, None, None

    def check_flush(self) -> None:
        """Warn once per flush_warn_s when the flush task has been stuck for longer than a ping
        interval. Called from status(), which the Runtime already polls every web.ingest.status_every_s."""
        if not self._flushing or self._flush_since is None:
            return
        now = self.clock()
        if now - self._flush_since < self.flush_warn_s:
            return
        if self._flush_warned_at is not None and now - self._flush_warned_at < self.flush_warn_s:
            return
        self._flush_warned_at = now
        log.warning("remote: the downlink flush has been stuck for %.0fs with %d message(s) queued; "
                    "treats and config pushes are not reaching the phone",
                    now - self._flush_since, len(self._queue))

    # -- status ---------------------------------------------------------------------------------
    def status(self) -> dict:
        """The pipeline.status() shape, with the phone's metrics instead of our own counters."""
        self.check_flush()
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
            "torch": self._torch_enabled,
            "privacy_mode": self._privacy_mode,
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
