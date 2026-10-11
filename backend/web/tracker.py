"""T3.4 Viewer tracking and anti-lurking: track active /video and /ws viewers, push policy downlinks with 300ms debounce, and support station/owner kick with 60s cooldown.
"""

from __future__ import annotations

import asyncio
import time
from typing import Any, Callable

from backend.web.modes import DEFAULT_PREVIEW, FOCUS_PREVIEW, GRID_PREVIEW

CLOSE_VIEWERS_KICKED = 4411


def parse_ua_label(ua: str | None) -> str:
    """Extract a user-agent family and OS label, truncated to at most 24 characters."""
    if not ua or not isinstance(ua, str):
        return "Viewer"
    ua_lower = ua.lower()
    os = (
        "Android"
        if "android" in ua_lower
        else "iOS"
        if ("iphone" in ua_lower or "ipad" in ua_lower)
        else "Mac"
        if "mac" in ua_lower
        else "Windows"
        if "windows" in ua_lower
        else "Linux"
        if "linux" in ua_lower
        else ""
    )
    browser = (
        "Edge"
        if "edg" in ua_lower
        else "Chrome"
        if "chrome" in ua_lower
        else "Safari"
        if "safari" in ua_lower
        else "Firefox"
        if "firefox" in ua_lower
        else "Browser"
    )
    label = f"{browser} on {os}".strip() if os else browser
    return label[:24]


class ViewersKickedError(Exception):
    """Raised when /video is requested while the kick cooldown is active."""


class ViewerTracker:
    """Tracks active video and WebSocket viewers for a household.

    When viewers change, a debounced (300 ms) `policy` downlink is posted to the phone station.
    Station or owner kick disconnects viewer WebSockets with code 4411 and imposes a 60-second cooldown
    during which /video is rejected.
    """

    def __init__(
        self,
        household_id: str,
        policy: Any,
        pipeline: Any,
        clock: Callable[[], float] = time.monotonic,
        debounce_s: float = 0.3,
    ) -> None:
        self.household_id = household_id
        self.policy = policy
        self.pipeline = pipeline
        self.clock = clock
        self.debounce_s = debounce_s
        self._video_streams: dict[str, dict[str, Any]] = {}
        self._ws_streams: dict[Any, dict[str, Any]] = {}
        self._audio_streams: dict[str, dict[str, Any]] = {}  # stream_id -> {viewer_id, label, started_at}
        self._demands: dict[str, dict[str, Any]] = {}  # viewer_id -> {"view": str, "expires_at": float}
        self._kicked_until: float = 0.0
        self._debounce_task: asyncio.Task | None = None
        self._on_kick: list[Callable[[], Any]] = []
        self._last_pushed_key: tuple | None = None

    def is_kicked(self) -> bool:
        return self.clock() < self._kicked_until

    def kicked_remaining_s(self) -> float:
        rem = self._kicked_until - self.clock()
        return max(0.0, rem)

    def register_kick_callback(self, cb: Callable[[], Any]) -> None:
        self._on_kick.append(cb)

    def enter_video(self, stream_id: str, viewer_id: str | None = None, label: str = "", view: str = "focus") -> None:
        if self.is_kicked():
            raise ViewersKickedError("viewers temporarily disconnected by station")
        lbl = (label or "Viewer").strip()[:24]
        self._video_streams[stream_id] = {
            "viewer_id": viewer_id or stream_id,
            "label": lbl,
            "view": view if view in ("grid", "focus") else "focus",
            "started_at": self.clock(),
        }
        self._on_change()

    def leave_video(self, stream_id: str) -> None:
        if stream_id in self._video_streams:
            del self._video_streams[stream_id]
            self._on_change()

    def enter_audio(self, stream_id: str, viewer_id: str | None = None, label: str = "") -> None:
        """A `/audio` listener (T3.9). Called from inside the socket handler; leave_audio pairs with it in
        `finally`. Refused while the station's kick cooldown runs, like enter_video."""
        if self.is_kicked():
            raise ViewersKickedError("viewers temporarily disconnected by station")
        lbl = (label or "Viewer").strip()[:24]
        self._audio_streams[stream_id] = {
            "viewer_id": viewer_id or stream_id,
            "label": lbl,
            "started_at": self.clock(),
        }
        self._on_change()

    def leave_audio(self, stream_id: str) -> None:
        if stream_id in self._audio_streams:
            del self._audio_streams[stream_id]
            self._on_change()

    def enter_ws(self, q: Any, viewer_id: str | None = None, label: str = "") -> None:
        lbl = (label or "Viewer").strip()[:24]
        self._ws_streams[q] = {
            "viewer_id": viewer_id or str(id(q)),
            "label": lbl,
            "started_at": self.clock(),
        }
        self._on_change()

    def leave_ws(self, q: Any) -> None:
        if q in self._ws_streams:
            del self._ws_streams[q]
            self._on_change()

    def _clean_expired_demands(self) -> bool:
        now = self.clock()
        expired = [vid for vid, d in self._demands.items() if d.get("expires_at", 0) <= now]
        if expired:
            for vid in expired:
                del self._demands[vid]
            return True
        return False

    def check_expired_demands(self) -> bool:
        if self._clean_expired_demands():
            self._on_change()
            return True
        return False

    def set_demand(self, viewer_id: str, view: str, ttl_s: float = 30.0) -> None:
        if view not in ("grid", "focus", "none"):
            raise ValueError(f"Invalid view demand: {view!r}")
        now = self.clock()
        if view == "none":
            self._demands.pop(viewer_id, None)
        else:
            self._demands[viewer_id] = {
                "view": view,
                "expires_at": now + ttl_s,
            }
            try:
                loop = asyncio.get_running_loop()

                async def _expire():
                    await asyncio.sleep(ttl_s + 0.05)
                    self.check_expired_demands()

                loop.create_task(_expire())
            except RuntimeError:
                pass
        self._on_change()

    @property
    def effective_demand(self) -> str:
        self._clean_expired_demands()
        views = {d["view"] for d in self._demands.values()} | {v.get("view", "focus") for v in self._video_streams.values()}
        if "focus" in views:
            return "focus"
        if "grid" in views:
            return "grid"
        return "none"

    @property
    def viewer_counts(self) -> dict[str, int]:
        self._clean_expired_demands()
        eff = self.effective_demand
        if eff == "none":
            return {"video": 0, "audio": 0}
        active_vids = set(self._demands.keys()) | {v["viewer_id"] for v in self._video_streams.values()}
        return {"video": len(active_vids), "audio": 0}

    @property
    def labels(self) -> list[str]:
        seen = set()
        for v in self._video_streams.values():
            seen.add(v["label"])
        for v in self._ws_streams.values():
            seen.add(v["label"])
        for v in self._audio_streams.values():
            seen.add(v["label"])
        return sorted(list(seen))

    def kick_all(self, cooldown_s: float = 60.0) -> int:
        """Disconnect all viewers: triggers callbacks and imposes cooldown."""
        self._kicked_until = self.clock() + cooldown_s
        count = len(self._video_streams) + len(self._ws_streams) + len(self._demands)
        self._video_streams.clear()
        self._audio_streams.clear()
        self._demands.clear()

        # Fire registered kick callbacks (disconnecting WebSocket subscribers)
        for cb in list(self._on_kick):
            try:
                cb()
            except Exception:
                pass

        self._push_policy_now()
        return count

    def _on_change(self) -> None:
        self._clean_expired_demands()
        eff = self.effective_demand
        if eff == "focus":
            target_preview = dict(FOCUS_PREVIEW)
        elif eff == "grid":
            target_preview = dict(GRID_PREVIEW)
        else:
            target_preview = getattr(self.policy, "preview", DEFAULT_PREVIEW)

        if hasattr(self.policy, "preview") and eff != "none":
            self.policy.preview = dict(target_preview)

        counts = self.viewer_counts
        if hasattr(self.policy, "viewers"):
            self.policy.viewers = dict(counts)

        if self.debounce_s <= 0:
            self._push_policy_now()
            return

        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            self._push_policy_now()
            return

        if self._debounce_task is not None and not self._debounce_task.done():
            self._debounce_task.cancel()

        async def _delayed():
            try:
                await asyncio.sleep(self.debounce_s)
                self._push_policy_now()
            except asyncio.CancelledError:
                pass

        self._debounce_task = loop.create_task(_delayed())

    def push_policy_now(self) -> None:
        """Immediately push policy downlink with current viewer counts to the station."""
        self._push_policy_now()

    def _push_policy_now(self) -> None:
        self._clean_expired_demands()
        eff = self.effective_demand
        if eff == "focus":
            target_preview = dict(FOCUS_PREVIEW)
        elif eff == "grid":
            target_preview = dict(GRID_PREVIEW)
        else:
            target_preview = getattr(self.policy, "preview", DEFAULT_PREVIEW)

        if hasattr(self.policy, "preview") and eff != "none":
            self.policy.preview = dict(target_preview)

        counts = self.viewer_counts
        if hasattr(self.policy, "viewers"):
            self.policy.viewers = dict(counts)

        set_policy_fn = getattr(self.pipeline, "set_policy", None)
        if set_policy_fn:
            mode = getattr(self.policy, "household_mode", "full")
            preview = dict(target_preview)
            key = (mode, tuple(sorted(preview.items())), tuple(sorted(counts.items())))
            if key != self._last_pushed_key:
                self._last_pushed_key = key
                try:
                    set_policy_fn(mode, preview=preview, viewers=counts)
                except Exception:
                    pass

