"""Mode-aware snapshots (privacy fix): a notification/LLM frame is exactly what the household is
entitled to keep — no pixels in privacy/blackout, the blurred frame in limited, raw in full."""

from __future__ import annotations

import io

from PIL import Image

from backend.contracts import EmotionState
from backend.notify.base import NotifyResult
from backend.web.hub import Hub
from backend.web.policy import Policy
from backend.web.runtime import Runtime
from backend.web.settings import load_config


class RecordingNotifier:
    is_external = False

    def __init__(self):
        self.jpegs = []

    async def send(self, state, jpeg):
        self.jpegs.append(jpeg)
        return NotifyResult("dashboard_only", "dashboard", "ok")


class RecordingStorage:
    def __init__(self):
        self.stored = []

    async def store_snapshot(self, jpeg, *, household_id, ts, emotion):
        self.stored.append(jpeg)
        return f"file://{len(self.stored)}" if jpeg else None


class StubPipeline:
    station_cap = "full"

    def __init__(self, jpeg: bytes):
        self._jpeg = jpeg

    def latest_frame_jpeg(self) -> bytes | None:
        return self._jpeg

    def raw_frame_jpeg(self) -> bytes | None:
        return self._jpeg


class NoLLM:
    enabled = False
    last_call = None


def _jpeg() -> bytes:
    img = Image.new("RGB", (320, 240), color=(120, 130, 200))
    b = io.BytesIO()
    img.save(b, format="JPEG", quality=90)
    return b.getvalue()


def make_runtime(pipeline: StubPipeline) -> Runtime:
    cfg = load_config("none.yaml", env={})
    runtime = Runtime(cfg, pipeline, NoLLM(), RecordingNotifier(), Hub(client_queue=8, history=4, frame_fps=8),
                      demo_clips=None, policy=Policy(household_mode=cfg["web"].get("mode", "full")))
    return runtime


def _bark_state() -> EmotionState:
    return EmotionState(ts=1000.0, emotion="fearful", confidence=0.9, source="rules", reason="unit-test")


def test_privacy_mode_notifies_with_no_snapshot_and_no_pixels():
    pipeline = StubPipeline(_jpeg())
    runtime = make_runtime(pipeline)
    runtime.storage = RecordingStorage()
    runtime.policy.set_mode("privacy")

    import asyncio
    notifier = RecordingNotifier()
    runtime.notifier = notifier
    asyncio.run(runtime._notify(_bark_state()))
    assert notifier.jpegs == [None]  # no photo leaves
    assert runtime.storage.stored == []  # skipped: no upload at all


def test_blackout_mode_notifies_with_no_snapshot_and_no_pixels():
    pipeline = StubPipeline(_jpeg())
    runtime = make_runtime(pipeline)
    runtime.storage = RecordingStorage()
    runtime.policy.set_mode("blackout")

    import asyncio
    notifier = RecordingNotifier()
    runtime.notifier = notifier
    asyncio.run(runtime._notify(_bark_state()))
    assert notifier.jpegs == [None]
    assert runtime.storage.stored == []  # skipped: no upload at all


def test_limited_mode_sends_the_blur_treated_frame_not_raw():
    pipeline = StubPipeline(_jpeg())
    runtime = make_runtime(pipeline)
    runtime.storage = RecordingStorage()
    from backend.web.limited import Limited

    runtime.limited = Limited(cache_s=0.0)
    runtime.policy.set_mode("limited")

    import asyncio
    notifier = RecordingNotifier()
    runtime.notifier = notifier
    asyncio.run(runtime._notify(_bark_state()))
    sent = notifier.jpegs[0]
    assert sent is not None
    assert sent != pipeline._jpeg  # treated: not the raw pixels
    assert runtime.storage.stored == [sent]  # the snapshot matches what was sent


def test_full_mode_still_sends_the_raw_frame():
    pipeline = StubPipeline(_jpeg())
    runtime = make_runtime(pipeline)
    runtime.storage = RecordingStorage()

    import asyncio
    notifier = RecordingNotifier()
    runtime.notifier = notifier
    asyncio.run(runtime._notify(_bark_state()))
    assert notifier.jpegs == [pipeline._jpeg]


def test_llm_frame_is_treated_by_mode_too():
    pipeline = StubPipeline(_jpeg())
    runtime = make_runtime(pipeline)
    runtime.policy.set_mode("privacy")
    jpeg, private = runtime._snapshot_jpeg()
    assert jpeg is None and private

    runtime.policy.set_mode("full")
    jpeg, private = runtime._snapshot_jpeg()
    assert jpeg == pipeline._jpeg and not private
