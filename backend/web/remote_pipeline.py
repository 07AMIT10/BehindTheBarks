"""RemotePipeline (`web.pipeline: remote`): the perception stack runs on the phone, so the server holds
no models, starts no watchdog and never ingests pixels itself. It is the Pipeline implementation the
event sink needs:

    run()               awaits until stop(): no source, no models, no watchdog thread
    ingest_frame()      the phone's 0x01 preview JPEG, so /video and notification snapshots work
    mark_treat()        pushes {"type": "treat"} back down, so the phone's rules see treat_event_recent
    status()            mirrors the phone's own metrics; "idle" before the first phone and after a
                        deliberate stop, "stalled" after web.remote.stale_s of camera silence

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


from backend.web.modes import DEFAULT_PREVIEW, ModeError, min_mode, parse_mode  # T3.1
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
        self._idle = False  # the phone stopped on purpose (or never showed up): not an error
        self._ever_attached = False
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
        # T3.1: the station cap is what the phone allows (it reports it); the policy mode is what
        # the household owner chose. The effective mode is the lower of the two -- the phone always wins.
        self._station_cap = "full"
        self._policy_mode = "full"
        # The last preview/audio/viewer values the policy tracker pushed. A mode change (POST /mode)
        # calls set_policy(mode) without them and must not reset viewers or preview on the phone.
        self._policy_preview: dict = dict(DEFAULT_PREVIEW)
        self._policy_audio = False
        self._policy_viewers: dict = {"video": 0, "audio": 0}
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
        if self._idle:
            return None  # nothing is streaming: no stale frame and no privacy slate either
        if self.effective_mode == "blackout":  # the policy engine decides, never a legacy flag
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

    def mark_stopped(self) -> None:
        """The phone said bye / closed with 1000: forget its last frame so the dashboard shows
        'idle' instead of 'stalled'."""
        self._jpeg, self._preview_ts, self._idle = None, None, True

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

    def set_policy(self, mode: str, *, preview: dict | None = None, audio: bool | None = None,
                   viewers: dict | None = None) -> bool:
        """T3.1: send one `policy` downlink (mode + preview params + audio + viewer counts), then
        the legacy `privacy` downlink so an old APK still mutes. Returns whether a phone took it.

        `mode` is validated here: an unknown mode raises rather than reaching the phone.
        `audio` is accepted for callers that predate T3.9 but never trusted: the flag sent is derived by
        `_audio_wanted()` (effective mode full AND at least one audio listener), so a mode change cannot
        leave a stale `audio: true` behind."""
        mode = parse_mode(mode)  # ModeError propagates: a bad mode must never be sent
        self._policy_mode = mode
        if preview is not None:
            self._policy_preview = dict(preview)
        if viewers is not None:
            self._policy_viewers = dict(viewers)
        self._policy_audio = self._audio_wanted()
        data = {"mode": mode, "preview": dict(self._policy_preview),
                "audio": self._policy_audio, "viewers": dict(self._policy_viewers)}
        sent = self._post({"type": "policy", "data": data})
        # Legacy compatibility: old APKs only understand `privacy`. New APKs act on `policy` and
        # ignore `privacy` once they have seen a `policy`, so sending both is safe.
        self._post({"type": "privacy", "data": {"enabled": self.effective_mode == "blackout"}})
        log.info("remote: sent policy=%s to phone (%s)", mode, "sent" if sent else "no phone")
        return sent

    def request_mode(self, mode: str, *, from_role: str = "owner") -> bool:
        """T3.3: ask the phone to raise its cap (mode_request downlink). Returns False with no phone."""
        mode = parse_mode(mode)
        sent = self._post({"type": "mode_request", "data": {"mode": mode, "from": from_role}})
        log.info("remote: sent mode_request=%s (from=%s) to phone (%s)", mode, from_role,
                 "sent" if sent else "no phone")
        return sent

    def raw_frame_jpeg(self) -> bytes | None:
        """The un-processed newest preview JPEG from the phone, or None if idle/none yet."""
        if self._idle:
            return None
        return self._jpeg

    def get_privacy(self) -> bool:
        """Legacy boolean view: True only when the effective mode is the total mute."""
        return self.effective_mode == "blackout"

    @property
    def effective_mode(self) -> str:
        """min(station cap, household policy). The phone's cap can only lower exposure here."""
        return min_mode(self._station_cap, self._policy_mode)

    @property
    def station_cap(self) -> str:
        return self._station_cap

    def set_station_cap(self, cap: str) -> None:
        """Called by the session when the phone reports a new cap (T3.1/T3.2). A cap that changes whether
        audio may flow is pushed at once, so the phone stops or starts its microphone stream."""
        self._station_cap = parse_mode(cap)
        if self._audio_wanted() != self._policy_audio:
            self.set_policy(self._policy_mode)

    def _audio_wanted(self) -> bool:
        """T3.9: the phone streams audio only in Full mode, and only while somebody listens."""
        return self.effective_mode == "full" and int(self._policy_viewers.get("audio", 0) or 0) > 0

    # -- phone session --------------------------------------------------------------------------
    def attach(self, session: Any, send: Callable[[dict], Awaitable[None]] | None) -> None:
        """The live phone session plus the downlink sender, wired up by the /ingest-events endpoint."""
        self._session, self._send = session, send
        self._ever_attached, self._idle = True, False
        # A newly attached phone gets the same grace a booting pipeline gets, so the dashboard
        # does not flash 'stalled' between the handshake and the phone's first preview frame.
        self._started_at = self.clock()
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
            "audio_frames": m["audio_frames"],
            "audio_bytes": m["audio_bytes"],
            "audio_dropped_policy": m["audio_dropped_policy"],
            "audio_dropped_invalid": m["audio_dropped_invalid"],
            "audio_clients": m["audio_clients"],
            "device": m["device"],
            "models": m["models"],
            "clock_offset_s": m["clock_offset_s"],
            "clock_samples": m["clock_samples"],
            "ping_due_s": m["ping_due_s"],
            "downlinks_sent": self.downlinks_sent,
            "downlinks_dropped": self.downlinks_dropped,
            "treats": len(self._treats),
            "torch": self._torch_enabled,
            "privacy_mode": self.effective_mode == "blackout",
            "station_cap": self._station_cap,
            "effective_mode": self.effective_mode,
            "policy_mode": self._policy_mode,
            "battery_pct": m.get("battery_pct"),
            "charging": m.get("charging"),
            "temp_c": m.get("temp_c"),
            "latest_telemetry": m.get("latest_telemetry"),
        }

    def _metrics(self, now: float) -> dict:
        if self._session is not None and hasattr(self._session, "metrics"):
            return self._session.metrics(now)
        return {"events_s": 0.0, "preview_fps": 0.0, "events": 0, "previews": 0, "dropped": 0, "device": None,
                "audio_frames": 0, "audio_bytes": 0, "audio_dropped_policy": 0, "audio_dropped_invalid": 0,
                "audio_clients": 0,
                "models": {}, "clock_offset_s": 0.0, "clock_samples": 0, "ping_due_s": None, "audio_ok": False,
                "last_event_age_s": None, "last_frame_age_s": None, "last_preview_age_s": None,
                "battery_pct": None, "charging": None, "temp_c": None, "latest_telemetry": None}

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
        # Idle is not an error: the phone stopped on purpose, or has never been here (first run).
        if self._idle or not self._ever_attached:
            return "idle"
        if self._paused(now):
            return "paused"  # a deliberate blackout: the phone is alive and silent on purpose
        age = self._frame_age_s(now)
        # Never having had a frame counts as stalled once the grace period is over (Pipeline._state).
        since = age if age is not None else now - self._started_at
        return "stalled" if since > self.stale_s else "running"

    def _paused(self, now: float) -> bool:
        """A blackout is silence on purpose (T3.2): the phone sends no frames, events or previews, but
        it keeps pinging. Paused = effective blackout AND a message (ping or any envelope) within two
        ping intervals. A blackout phone that went quiet is stalled, like a phone in any other mode."""
        s = self._session
        if s is None or self.effective_mode != "blackout" or not hasattr(s, "idle_s"):
            return False
        ping_every = float(getattr(s, "ping_every_s", 30.0))
        return s.idle_s(now) <= 2 * ping_every
