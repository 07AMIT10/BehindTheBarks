"""Fan-out of envelopes to WebSocket clients. publish() is sync and never blocks: each client has a
bounded queue and a full queue drops its oldest message (a slow client loses data, not the server)."""

from __future__ import annotations

import asyncio
import time
from collections import deque
from typing import Any

from pydantic import BaseModel

from backend.web.event_log import EventLog

MESSAGE_TYPES = ("frame", "audio", "rules", "emotion", "llm", "notification", "treat", "status")


class Hub:
    def __init__(self, client_queue: int = 64, history: int = 2000, frame_fps: float = 8.0,
                 clock=time.time, log: EventLog | None = None) -> None:
        self._qsize, self._clock, self._log = client_queue, clock, log
        self._clients: set[asyncio.Queue] = set()
        self._history: deque[dict] = deque(maxlen=history)
        self._seq = 0
        self._frame_period = 1.0 / frame_fps if frame_fps > 0 else 0.0
        self._last_frame = -1e18

    @property
    def client_count(self) -> int:
        return len(self._clients)

    def subscribe(self) -> asyncio.Queue:
        q: asyncio.Queue = asyncio.Queue(maxsize=self._qsize)
        self._clients.add(q)
        return q

    def unsubscribe(self, q: asyncio.Queue) -> None:
        self._clients.discard(q)

    def publish(self, type: str, data: BaseModel | dict[str, Any], meta: dict | None = None) -> dict | None:
        if type not in MESSAGE_TYPES:
            raise ValueError(f"unknown message type {type!r}")
        now = self._clock()
        if type == "frame":
            if now - self._last_frame < self._frame_period - 1e-9:
                return None
            self._last_frame = now
        self._seq += 1
        env: dict[str, Any] = {"type": type, "seq": self._seq, "ts": now,
                               "data": data.model_dump(mode="json") if isinstance(data, BaseModel) else data}
        if meta:
            env["meta"] = meta
        if type not in ("frame", "status"):  # status is live-only: GET /status is the source of truth
            self._history.append(env)
        if self._log:
            self._log.write(env)
        for q in list(self._clients):
            if q.full():
                try:
                    q.get_nowait()
                except asyncio.QueueEmpty:
                    pass
            q.put_nowait(env)
        return env

    def history(self, since: int = 0) -> list[dict]:
        return [m for m in self._history if m["seq"] > since]

    def close(self) -> None:
        if self._log:
            self._log.close()
