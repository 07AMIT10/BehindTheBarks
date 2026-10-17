"""Live audio fan-out for one station channel (T3.9, A3.2).

The phone's 0x02 frames arrive in RemoteSession.handle and are published here; every `/audio` socket of
the same station holds one bounded queue. Memory is bounded: a listener that cannot keep up loses its
OLDEST frames (2 s of audio at 10 frames/s), never the phone's connection and never another listener.

Pure asyncio, no sockets: main.py owns the WebSocket, RemoteSession owns validation and the policy gate.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass

AUDIO_QUEUE_FRAMES = 20  # 2 s at 10 frames/s per listener

KICKED_AUDIO = object()  # the station or owner kicked viewers (close 4411)
CLIENT_GONE = object()  # the listener's socket was closed by the client (the watcher saw it)


@dataclass
class _Sub:
    """One listener. Owners are not kicked without asking; viewer subs carry the viewer's id so a
    revoke can close exactly their sockets (the /audio relay is never re-validated after connect)."""

    owner: bool = False
    viewer_id: str | None = None


def _put_drop_oldest(q: asyncio.Queue, item: object) -> None:
    """Enqueue without ever blocking the publisher: a full queue loses its oldest entry first."""
    while True:
        try:
            q.put_nowait(item)
            return
        except asyncio.QueueFull:
            try:
                q.get_nowait()
            except asyncio.QueueEmpty:  # pragma: no cover - cannot be full and empty at once
                return


class AudioHub:
    """Fan-out of one station's phone frames (`codec|seq|t_ms|payload`, without the 0x02 byte)."""

    def __init__(self, maxlen: int = AUDIO_QUEUE_FRAMES) -> None:
        self.maxlen = maxlen
        self.last_codec: int | None = None  # codec of the newest frame: a new listener starts on it
        self._subs: dict[asyncio.Queue, _Sub] = {}  # queue -> whose socket it serves

    @property
    def client_count(self) -> int:
        return len(self._subs)

    def subscribe(self, *, owner: bool = False, viewer_id: str | None = None) -> asyncio.Queue:
        q: asyncio.Queue = asyncio.Queue(maxsize=self.maxlen)
        self._subs[q] = _Sub(owner=bool(owner), viewer_id=viewer_id)
        return q

    def unsubscribe(self, q: asyncio.Queue) -> None:
        self._subs.pop(q, None)

    def publish(self, frame: bytes) -> None:
        """One relayed frame: `codec u8 | seq u32 LE | t_ms u32 LE | payload`. Never raises."""
        if not frame:
            return
        self.last_codec = frame[0]
        for q in list(self._subs):
            _put_drop_oldest(q, frame)

    def kick_viewer(self, viewer_id: str | None) -> int:
        """Disconnect exactly the named viewer's own listeners (close 4411); owners stay."""
        if not viewer_id:
            return 0
        count = 0
        for q, sub in list(self._subs.items()):
            if sub.owner or sub.viewer_id != viewer_id:
                continue
            self._subs.pop(q, None)
            _put_drop_oldest(q, KICKED_AUDIO)
            count += 1
        return count

    def kick_all(self, include_owners: bool = False) -> int:
        """Disconnect listeners (close 4411). Owners stay, as they do on the video kick."""
        count = 0
        for q, sub in list(self._subs.items()):
            if sub.owner and not include_owners:
                continue
            self._subs.pop(q, None)
            _put_drop_oldest(q, KICKED_AUDIO)
            count += 1
        return count
