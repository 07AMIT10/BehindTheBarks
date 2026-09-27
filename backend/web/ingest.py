"""Phone ingest protocol (/ingest): hello parsing, binary message dispatch, received-fps metering.

Pure and synchronous: no sockets and no asyncio here, so every rule is unit-testable. main.py owns the
WebSocket; Runtime owns "which phone is the active one".

Protocol (CLAUDE.md, "Phone camera"):
  1. text   {"type": "hello", "width", "height", "fps", "sample_rate", "device"
             [, "facing": "back" | "front"] [, "camera": true | false]}
  2. binary 0x01 + JPEG                    -> pipeline.ingest_frame(jpeg, ts)
            0x02 + mono Int16 LE PCM       -> pipeline.ingest_audio(pcm, hello.sample_rate, ts)
  ts is time.time() at receipt on the server.
"""

from __future__ import annotations

import json
import time
from collections import deque
from dataclasses import dataclass
from typing import Any, Callable

KIND_FRAME = 0x01
KIND_AUDIO = 0x02

CLOSE_BAD_HELLO = 4400  # first message missing or not a valid hello
CLOSE_REPLACED = 4408   # this phone went quiet and a new phone took over
CLOSE_BUSY = 4409       # another phone is already streaming

MAX_DEVICE_LEN = 64


class HelloError(ValueError):
    """The first /ingest message is not a valid hello. str(err) is sent as the close reason."""


@dataclass(frozen=True)
class Hello:
    width: int
    height: int
    fps: float
    sample_rate: int
    device: str
    facing: str | None = None
    camera: bool = True


def _num(msg: dict, key: str, lo: float, hi: float) -> float:
    v = msg.get(key)
    if isinstance(v, bool) or not isinstance(v, (int, float)) or not lo <= v <= hi:
        raise HelloError(f"hello.{key} must be a number in [{lo:g}, {hi:g}]")
    return v


def parse_hello(text: str | None) -> Hello:
    try:
        msg = json.loads(text) if text else None
    except (TypeError, ValueError):
        raise HelloError("first message must be the JSON hello") from None
    if not isinstance(msg, dict) or msg.get("type") != "hello":
        raise HelloError('first message must be {"type": "hello", ...}')
    device = msg.get("device")
    if not isinstance(device, str) or not device.strip():
        raise HelloError("hello.device must be a non-empty string")
    facing = msg.get("facing")
    if facing not in (None, "back", "front"):
        raise HelloError('hello.facing must be "back" or "front"')
    camera = msg.get("camera", True)
    if not isinstance(camera, bool):
        raise HelloError("hello.camera must be true or false")
    return Hello(
        width=int(_num(msg, "width", 0, 8192)),
        height=int(_num(msg, "height", 0, 8192)),
        fps=float(_num(msg, "fps", 0, 60)),
        sample_rate=int(_num(msg, "sample_rate", 8000, 192000)),
        device=device.strip()[:MAX_DEVICE_LEN],
        facing=facing,
        camera=camera,
    )


class FpsMeter:
    """Events per second over a sliding window: (n - 1) / (newest - oldest) of the events inside it."""

    def __init__(self, window_s: float = 3.0) -> None:
        self.window_s = window_s
        self._ts: deque[float] = deque()

    def add(self, ts: float) -> None:
        self._ts.append(ts)
        self._trim(ts)

    def _trim(self, now: float) -> None:
        while self._ts and self._ts[0] < now - self.window_s:
            self._ts.popleft()

    def rate(self, now: float) -> float:
        self._trim(now)
        if len(self._ts) < 2 or self._ts[-1] <= self._ts[0]:
            return 0.0
        return round((len(self._ts) - 1) / (self._ts[-1] - self._ts[0]), 1)


class IngestSession:
    """One connected phone. handle() is O(1) apart from one slice copy, and never raises."""

    def __init__(self, hello: Hello, pipeline: Any, clock: Callable[[], float] = time.time,
                 fps_window_s: float = 3.0, max_message_bytes: int = 2_000_000) -> None:
        self.hello, self.pipeline, self.clock = hello, pipeline, clock
        self.max_message_bytes = max_message_bytes
        self.connected_at = clock()
        self.last_msg_ts = self.connected_at
        self.last_frame_ts: float | None = None
        self.last_audio_ts: float | None = None
        self.frames = 0
        self.audio_chunks = 0
        self.dropped = 0
        self.connected = True
        self._fps = FpsMeter(fps_window_s)

    def handle(self, data: bytes) -> str | None:
        """Dispatch one binary message. Returns "frame", "audio", or None when it was dropped."""
        if not self.connected:  # replaced by a newer phone: its data no longer counts
            return None
        ts = self.clock()
        self.last_msg_ts = ts
        if len(data) < 2 or len(data) > self.max_message_bytes:
            self.dropped += 1
            return None
        kind, payload = data[0], data[1:]
        try:
            if kind == KIND_FRAME:
                self.pipeline.ingest_frame(payload, ts)
                self.frames += 1
                self.last_frame_ts = ts
                self._fps.add(ts)
                return "frame"
            if kind == KIND_AUDIO:
                self.pipeline.ingest_audio(payload, self.hello.sample_rate, ts)
                self.audio_chunks += 1
                self.last_audio_ts = ts
                return "audio"
        except Exception:  # noqa: BLE001 - ingest_* must not raise; if one does, drop the message
            self.dropped += 1
            return None
        self.dropped += 1
        return None

    def idle_s(self, now: float) -> float:
        return max(0.0, now - self.last_msg_ts)

    def snapshot(self, now: float) -> dict:
        h = self.hello
        age = None if self.last_frame_ts is None else round(max(0.0, now - self.last_frame_ts), 2)
        return {
            "connected": self.connected, "device": h.device, "facing": h.facing, "camera": h.camera,
            "fps": self._fps.rate(now) if self.connected else 0.0,
            "width": h.width, "height": h.height, "sample_rate": h.sample_rate, "last_frame_age_s": age,
        }
