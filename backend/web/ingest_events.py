"""Phone-producer event sink (/ingest-events): the Android app runs the perception stack and the
backend only receives its events. See CLAUDE.md, "On-device phone (event sink)".

Pure and synchronous like backend/web/ingest.py, so every rule is unit-testable without a socket:
main.py owns the WebSocket, Runtime owns "which phone is the active one", and the session owns the
clock offset between the phone's clock and ours.

Protocol (phone -> server):
  1. text    {"type": "hello", "proto": 1, "device": ..., "phone_time": <epoch s>,
              "models": {...}, "profile": "mobile"}
     reply  {"type": "hello_ack", "server_time": <epoch s>}
  2. text    {"type": "frame" | "audio" | "rules", "data": {<the JSON contract>}}
     text    {"type": "treat", "data": {"ts": <epoch s>}}          (optional ts)
     text    {"type": "ping", "phone_time": <epoch s>}   ->  reply {"type": "pong", "server_time": ...}
  3. binary  0x01 + JPEG                    (preview frame, for /video and notification snapshots)

`data.ts` is the phone's clock. It is rebased by `server_time_at_hello - phone_time` before anything
else looks at it, and refreshed by every ping as the median of the last 5 samples, so one late or
early ping cannot move the whole stream. The phone's own `treat` envelope is stamped like the others
and echoed back down as the treat downlink (idempotent on the phone: it must not re-send one it
received).

server -> phone: {"type": "treat", "ts": ...} and {"type": "config", "data": {"rules": {...}}}.
"""

from __future__ import annotations

import json
import math
import statistics
import time
from collections import deque
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable

from backend.contracts import AudioEvent, FrameEvent, RulesLabel
from backend.web.ingest import CLOSE_BAD_HELLO, CLOSE_BUSY, CLOSE_REPLACED, FpsMeter, HelloError, MAX_DEVICE_LEN

PROTO = 1
KIND_PREVIEW = 0x01

EVENT_MODELS: dict[str, type] = {"frame": FrameEvent, "audio": AudioEvent, "rules": RulesLabel}

AUDIO_OK_S = 2.0  # an AudioEvent within this many seconds means "the mic is alive", as in Pipeline
MAX_SKEW_S = 60.0  # an envelope whose rebased ts is further than this from our clock is dropped
MIN_EPOCH = 1_000_000_000.0   # 2001-09-09: below this the phone sent something other than seconds
MAX_EPOCH = 4_102_444_800.0   # 2100-01-01: above this, most likely milliseconds
MAX_MODELS = 32
MAX_NAME_LEN = 48


@dataclass(frozen=True)
class RemoteHello:
    device: str
    phone_time: float
    models: dict[str, Any] = field(default_factory=dict)
    profile: str = "mobile"
    proto: int = PROTO
    token: str | None = None


def _num(msg: dict, key: str, lo: float, hi: float) -> float:
    v = msg.get(key)
    if isinstance(v, bool) or not isinstance(v, (int, float)) or not lo <= v <= hi:
        raise HelloError(f"hello.{key} must be a number in [{lo:g}, {hi:g}]")
    return float(v)


def _name(msg: dict, key: str, hi: int, default: str) -> str:
    """A trimmed non-empty string of at most `hi` characters (longer device names are cut, as in
    backend/web/ingest.py)."""
    v = msg.get(key, default)
    if not isinstance(v, str) or not v.strip():
        raise HelloError(f"hello.{key} must be a non-empty string of at most {hi} characters")
    return v.strip()[:hi]


def _models(msg: dict) -> dict[str, Any]:
    v = msg.get("models", {})
    if not isinstance(v, dict) or len(v) > MAX_MODELS:
        raise HelloError(f"hello.models must be an object of at most {MAX_MODELS} entries")
    for name, ver in v.items():
        if not isinstance(name, str) or not name.strip() or len(name) > MAX_NAME_LEN:
            raise HelloError(f"hello.models keys must be non-empty strings of at most {MAX_NAME_LEN} characters")
        if not (ver is None or isinstance(ver, (str, int, float, bool))):
            raise HelloError(f"hello.models.{name} must be a version string or number")
    return dict(v)


def parse_remote_hello(text: str | None) -> RemoteHello:
    """The first /ingest-events message. Raises HelloError, whose str(err) is the close reason."""
    try:
        msg = json.loads(text) if text else None
    except (TypeError, ValueError):
        raise HelloError("first message must be the JSON hello") from None
    if not isinstance(msg, dict) or msg.get("type") != "hello":
        raise HelloError('first message must be {"type": "hello", "proto": %d, ...}' % PROTO)
    proto = msg.get("proto", PROTO)
    if isinstance(proto, bool) or not isinstance(proto, int) or proto != PROTO:
        raise HelloError(f"hello.proto must be {PROTO}")
    tok = msg.get("token")
    token_str = str(tok).strip() if tok is not None and isinstance(tok, str) else None
    return RemoteHello(
        device=_name(msg, "device", MAX_DEVICE_LEN, ""),
        phone_time=_num(msg, "phone_time", MIN_EPOCH, MAX_EPOCH),
        models=_models(msg),
        profile=_name(msg, "profile", MAX_NAME_LEN, "mobile"),
        proto=proto,
        token=token_str,
    )


class ClockOffset:
    """server_ts = phone_ts + offset. Seeded by the hello, refreshed by the pings.

    The offset is the median of the last `samples` one-way samples (server receipt - phone send), so a
    single ping that was delayed or sent with a bad clock cannot move the whole stream. The hello
    sample counts as the first one.
    """

    def __init__(self, samples: int = 5) -> None:
        self.samples = max(1, int(samples))
        self._window: deque[float] = deque(maxlen=self.samples)
        self._offset = 0.0

    @property
    def offset(self) -> float:
        return self._offset

    @property
    def count(self) -> int:
        return len(self._window)

    def seed(self, server_time: float, phone_time: float) -> float:
        self._window.clear()
        return self.add(server_time, phone_time)

    def add(self, server_time: float, phone_time: float) -> float:
        self._window.append(float(server_time) - float(phone_time))
        self._offset = float(statistics.median(self._window))
        return self._offset

    def rebase(self, phone_ts: float) -> float:
        return float(phone_ts) + self._offset


class RemoteSession:
    """One connected phone producing events. handle()/handle_text() never raise: an envelope that
    fails validation is counted in `dropped` and thrown away, so one bad message never closes the
    stream (only a bad hello or a lost socket does that)."""

    def __init__(self, hello: RemoteHello, pipeline: Any, runtime: Any, clock: Callable[[], float] = time.time,
                 *, max_message_bytes: int = 2_000_000, fps_window_s: float = 3.0, offset_samples: int = 5,
                 ping_every_s: float = 30.0, max_skew_s: float = MAX_SKEW_S) -> None:
        self.hello, self.pipeline, self.runtime, self.clock = hello, pipeline, runtime, clock
        self.max_message_bytes = max_message_bytes
        self.ping_every_s = ping_every_s
        self.max_skew_s = max_skew_s
        self.connected = True
        self.server_time_at_hello = clock()
        self.connected_at = self.server_time_at_hello
        self.last_msg_ts = self.server_time_at_hello
        self.last_frame_ts: float | None = None  # receipt times: ages are measured on our clock
        self.last_audio_ts: float | None = None
        self.last_event_ts: float | None = None
        self.last_preview_ts: float | None = None
        self.last_ping_ts: float | None = None
        self.frames = 0
        self.audio_events = 0
        self.rules_labels = 0
        self.treats = 0
        self.pings = 0
        self.previews = 0
        self.dropped = 0
        self.offset = ClockOffset(offset_samples)
        self.offset.seed(self.server_time_at_hello, hello.phone_time)
        self._fps = FpsMeter(fps_window_s)
        self._preview_fps = FpsMeter(fps_window_s)

    @property
    def events(self) -> int:
        return self.frames + self.audio_events + self.rules_labels

    # -- uplink ----------------------------------------------------------------------------
    def handle_text(self, text: str | None) -> str | None:
        """Dispatch one JSON envelope. Returns its type, or None when it was dropped."""
        if not self.connected:
            return None
        now = self.clock()
        self.last_msg_ts = now
        try:
            msg = json.loads(text) if text else None
        except (TypeError, ValueError):
            self.dropped += 1
            return None
        if not isinstance(msg, dict):
            self.dropped += 1
            return None
        kind = msg.get("type")
        if kind == "ping":
            return self._ping(msg, now)
        data = msg.get("data")
        if kind == "treat":
            return self._treat(data, now)
        model = EVENT_MODELS.get(kind) if isinstance(kind, str) else None
        if model is None or not isinstance(data, dict):
            self.dropped += 1
            return None
        try:
            ev = model.model_validate(self._rebased(data, now))
        except ValueError:  # a pydantic ValidationError is a ValueError: bad ts, extra key, bad enum
            self.dropped += 1
            return None
        if kind == "frame":
            dispatch, counter, ts_field = self.runtime.on_frame, "frames", "last_frame_ts"
        elif kind == "audio":
            dispatch, counter, ts_field = self.runtime.on_audio, "audio_events", "last_audio_ts"
        else:
            dispatch, counter, ts_field = self.runtime.on_rules, "rules_labels", None
        try:
            dispatch(ev)
        except Exception:  # noqa: BLE001 - a Runtime that raises costs one envelope, never the socket
            self.dropped += 1
            return None
        setattr(self, counter, getattr(self, counter) + 1)
        if ts_field is not None:
            setattr(self, ts_field, now)
        self.last_event_ts = now
        self._fps.add(now)
        return kind

    def handle(self, data: bytes) -> str | None:
        """Dispatch one binary message: 0x01 + JPEG preview. Returns "preview" or None."""
        if not self.connected:
            return None
        now = self.clock()
        self.last_msg_ts = now
        if len(data) < 2 or len(data) > self.max_message_bytes or data[0] != KIND_PREVIEW:
            self.dropped += 1
            return None
        try:
            self.pipeline.ingest_frame(data[1:], now)
        except Exception:  # noqa: BLE001 - ingest_frame must not raise; if one does, drop the message
            self.dropped += 1
            return None
        self.previews += 1
        self.last_preview_ts = now
        self._preview_fps.add(now)
        return "preview"

    def _rebased(self, data: dict, now: float) -> dict:
        """The envelope's ts, moved onto the server clock. A ts that is not an epoch number, or that
        lands implausibly far from our own clock once rebased (a buggy app sending milliseconds, a
        stale spool, or an epoch-year typo), is rejected rather than trusted: everything downstream
        (`no_dog_unknown_s`, `llm_stale_s`, cooldowns, the stall detector) compares against our clock,
        so one bad ts would freeze the whole dashboard in a permanently "live" state."""
        ts = data.get("ts")
        if isinstance(ts, bool) or not isinstance(ts, (int, float)) or not math.isfinite(ts):
            raise ValueError("ts must be an epoch number")
        rebased = self.offset.rebase(float(ts))
        if abs(rebased - now) > self.max_skew_s:
            raise ValueError(f"ts is {rebased - now:+.0f}s from the server clock, over {self.max_skew_s:g}s")
        return {**data, "ts": rebased}

    def _ping(self, msg: dict, now: float) -> str | None:
        ts = msg.get("phone_time")
        if isinstance(ts, bool) or not isinstance(ts, (int, float)) or not math.isfinite(ts):
            self.dropped += 1
            return None
        self.offset.add(now, float(ts))
        self.pings += 1
        self.last_ping_ts = now
        return "ping"

    def _treat(self, data: Any, now: float) -> str | None:
        """A treat envelope has no pydantic contract: `{"ts"}` is optional and stamped at receipt."""
        data = {} if data is None else data
        if not isinstance(data, dict):
            self.dropped += 1
            return None
        try:
            ts = self._rebased(data, now)["ts"] if "ts" in data else now
        except ValueError:
            self.dropped += 1
            return None
        try:
            self.runtime.treat(ts)
        except Exception:  # noqa: BLE001 - as above: one envelope, not the socket
            self.dropped += 1
            return None
        self.treats += 1
        return "treat"

    # -- downlink / status ------------------------------------------------------------------
    def idle_s(self, now: float) -> float:
        return max(0.0, now - self.last_msg_ts)

    def ping_due_s(self, now: float) -> float:
        """Seconds until the next expected ping; negative means the phone is overdue (clock drift, or
        the socket is half-open). Counted from the hello when no ping has arrived yet."""
        since = now - (self.last_ping_ts if self.last_ping_ts is not None else self.server_time_at_hello)
        return round(self.ping_every_s - since, 1)

    def _age(self, ts: float | None, now: float) -> float | None:
        return None if ts is None else round(max(0.0, now - ts), 2)

    def snapshot(self, now: float) -> dict:
        """The dashboard's phone card (/status -> phone)."""
        h = self.hello
        return {
            "connected": self.connected, "device": h.device, "transport": "ingest-events", "camera": True,
            "facing": None,  # the app has one camera; the card shape stays the /ingest one
            "profile": h.profile, "proto": h.proto, "models": h.models,
            "fps": self._fps.rate(now) if self.connected else 0.0,
            "preview_fps": self._preview_fps.rate(now) if self.connected else 0.0,
            "events": self.events, "previews": self.previews, "treats": self.treats, "pings": self.pings,
            "dropped": self.dropped,
            "clock_offset_s": round(self.offset.offset, 3), "clock_samples": self.offset.count,
            "ping_due_s": self.ping_due_s(now),
            "last_event_age_s": self._age(self.last_event_ts, now),
            "last_frame_age_s": self._age(self.last_frame_ts, now),
        }

    def metrics(self, now: float) -> dict:
        """What RemotePipeline.status() mirrors."""
        return {
            "events_s": self._fps.rate(now), "preview_fps": self._preview_fps.rate(now),
            "events": self.events, "previews": self.previews, "dropped": self.dropped,
            "device": self.hello.device, "models": self.hello.models,
            "clock_offset_s": round(self.offset.offset, 3), "clock_samples": self.offset.count,
            "ping_due_s": self.ping_due_s(now),
            "last_event_age_s": self._age(self.last_event_ts, now),
            "last_frame_age_s": self._age(self.last_frame_ts, now),
            "last_preview_age_s": self._age(self.last_preview_ts, now),
            "audio_ok": self.last_audio_ts is not None and now - self.last_audio_ts <= AUDIO_OK_S,
        }


def bind_pipeline(pipeline: Any, session: RemoteSession, send: Callable[[dict], Awaitable[None]]) -> bool:
    """Give the live session and the downlink sender to the pipeline, if it wants them (RemotePipeline
    does; MockPipeline and Pipeline do not, and then the endpoint just feeds envelopes to the Runtime).
    Returns whether the pipeline took the session."""
    attach = getattr(pipeline, "attach", None)
    if attach is None:
        return False
    attach(session, send)
    return True
