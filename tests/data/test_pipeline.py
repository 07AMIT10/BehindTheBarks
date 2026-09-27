import asyncio
import copy
import logging
import shutil
import subprocess
import threading
import time

import cv2
import numpy as np
import pytest

from backend import pipeline as P
from backend.contracts import EMOTIONS, AudioEvent, FrameEvent, RulesLabel
from backend.pipeline import Pipeline
from backend.sources import MediaSources
from backend.vision.detect import Detection

CFG = {
    "data": {
        "fps": 10,
        "keypoint_conf_threshold": 0.3,
        "feeding_zone": [[0.0, 0.0], [1.0, 0.0], [1.0, 1.0], [0.0, 1.0]],
        "source": {"type": "browser", "stall_s": 2.0},
        "rules": {"treat_window_s": 1.0, "hysteresis_s": 0.0},
    }
}


def cfg(**source):
    c = copy.deepcopy(CFG)
    c["data"]["source"].update(source)
    return c


class FakeDetector:
    def __init__(self, dog=True):
        self.dog = dog

    def detect(self, frame):
        if not self.dog:
            return None
        bbox = (20.0, 20.0, 120.0, 100.0)
        return Detection(bbox=bbox, conf=0.9, in_feeding_zone=True, raw_bbox=bbox)


class FakePose:
    def estimate(self, frame, bbox):
        return {}  # features all null: keeps the scores about the treat, not the posture


class FakeFace:
    def estimate(self, frame, kps):
        return None


class FakeAudio:
    """One bark event per 4 chunks, stamped at the end of the chunk."""

    def __init__(self):
        self.n = 0

    def push(self, ts, chunk):
        self.n += 1
        return [AudioEvent(ts=ts + len(chunk) / 16_000, label="bark", score=0.8)] if self.n % 4 == 0 else []


def parts(**over):
    return {"detector": FakeDetector(), "pose": FakePose(), "face": FakeFace(), "audio": FakeAudio(), **over}


def jpeg(level=90, size=(160, 120)):
    ok, buf = cv2.imencode(".jpg", np.full((size[1], size[0], 3), level, np.uint8))
    return buf.tobytes()


class Collector:
    def __init__(self):
        self.frames: list[FrameEvent] = []
        self.audio: list[AudioEvent] = []
        self.rules: list[RulesLabel] = []
        self.threads: set[int] = set()

    def on_frame(self, e):
        self.threads.add(threading.get_ident())
        self.frames.append(e)

    def on_audio(self, e):
        self.threads.add(threading.get_ident())
        self.audio.append(e)

    def on_rules(self, e):
        self.threads.add(threading.get_ident())
        self.rules.append(e)


async def feed_phone(pipe, seconds, fps=10, with_audio=True):
    """Push what a phone would: frames at fps and 100 ms PCM chunks, in real time."""
    end = time.monotonic() + seconds
    i = 0
    while time.monotonic() < end:
        pipe.ingest_frame(jpeg(), time.time())
        if with_audio:
            pipe.ingest_audio((np.sin(np.arange(4800) / 10) * 8000).astype("<i2").tobytes(), 48_000, time.time())
        i += 1
        await asyncio.sleep(1 / fps)


# -- browser mode ------------------------------------------------------------------------------


def test_events_arrive_on_the_callers_loop_and_status_tracks_them():
    async def go():
        pipe = Pipeline(cfg(), components=parts())
        col = Collector()
        task = asyncio.create_task(pipe.run(col.on_frame, col.on_audio, col.on_rules))
        await asyncio.sleep(0.05)
        assert pipe.status()["state"] == "running" and pipe.latest_frame_jpeg() is None
        await feed_phone(pipe, 2.5)
        st = pipe.status()
        img = pipe.latest_frame_jpeg()
        pipe.stop()
        await asyncio.wait_for(task, 5)
        return pipe, col, st, img

    pipe, col, st, img = asyncio.run(go())
    assert len(col.frames) >= 8 and len(col.rules) == len(col.frames)
    assert col.threads == {threading.get_ident()}  # every callback ran on the event loop's thread
    assert all(f.dog_detected and f.source == "live" for f in col.frames)
    assert [f.ts for f in col.frames] == sorted(f.ts for f in col.frames)
    assert all(set(r.scores) == set(EMOTIONS) for r in col.rules)
    assert len(col.audio) >= 1 and all(a.label == "bark" for a in col.audio)
    assert st["source"] == "browser" and st["state"] == "running" and st["audio_ok"] is True
    assert st["fps"] > 3 and 0 <= st["last_frame_age_s"] < 0.5
    assert cv2.imdecode(np.frombuffer(img, np.uint8), cv2.IMREAD_COLOR).shape == (120, 160, 3)  # full resolution, no overlay
    assert pipe.status()["state"] == "stopped"


def test_a_stalled_phone_drops_the_rules_to_unknown_and_recovers(monkeypatch):
    monkeypatch.setattr(P, "WATCHDOG_TICK_S", 0.1)
    c = cfg(stall_s=0.4)
    c["data"]["rules"]["no_dog_unknown_s"] = 0.4

    async def go():
        pipe = Pipeline(c, components=parts())
        col = Collector()
        task = asyncio.create_task(pipe.run(col.on_frame, col.on_audio, col.on_rules))
        await feed_phone(pipe, 1.0)
        assert pipe.status()["state"] == "running"
        n_before = len(col.frames)
        await asyncio.sleep(1.5)  # the phone goes quiet
        stalled = pipe.status()
        during = (col.frames[n_before:], col.rules[n_before:])
        await feed_phone(pipe, 1.0)  # ...and comes back, no restart
        recovered = pipe.status()
        pipe.stop()
        await asyncio.wait_for(task, 5)
        return stalled, during, recovered, col

    stalled, (frames, rules), recovered, col = asyncio.run(go())
    assert stalled["state"] == "stalled" and stalled["last_frame_age_s"] > 0.4
    assert frames and not any(f.dog_detected for f in frames)  # the watchdog's "no dog" frames
    assert rules[-1].emotion == "unknown" and rules[-1].scores["unknown"] == 1.0
    assert recovered["state"] == "running"
    assert col.frames[-1].dog_detected


def test_never_connected_browser_is_stalled_after_the_grace_period():
    async def go():
        pipe = Pipeline(cfg(stall_s=0.3), components=parts())
        col = Collector()
        task = asyncio.create_task(pipe.run(col.on_frame, col.on_audio, col.on_rules))
        await asyncio.sleep(0.1)
        first = pipe.status()
        await asyncio.sleep(0.5)
        second = pipe.status()
        pipe.stop()
        await asyncio.wait_for(task, 5)
        return first, second

    first, second = asyncio.run(go())
    assert first["state"] == "running" and second["state"] == "stalled"
    assert second["last_frame_age_s"] is None and second["audio_ok"] is False


def test_mic_or_audio_pipeline_failing_drops_audio_but_not_video():
    """Step 13 degradation: a dead mic/audio path ends the audio thread quietly; video keeps running."""

    class RaisingAudio:
        def push(self, ts, chunk):
            raise RuntimeError("audio pipeline exploded")

    async def go():
        pipe = Pipeline(cfg(stall_s=0.2), components=parts(audio=RaisingAudio()))
        col = Collector()
        task = asyncio.create_task(pipe.run(col.on_frame, col.on_audio, col.on_rules))
        await feed_phone(pipe, 1.0)
        st = pipe.status()
        pipe.stop()
        await asyncio.wait_for(task, 5)
        return col, st

    col, st = asyncio.run(go())
    assert len(col.frames) >= 5 and len(col.rules) == len(col.frames)  # video is unaffected
    assert col.audio == []  # no audio events, ever
    assert st["state"] == "running" and st["audio_ok"] is False  # audio marked down, pipeline otherwise fine


def test_pose_or_face_model_failing_still_emits_detection_only_frame_events():
    """Step 13 degradation: a broken pose/face model must not take the frame's detection down with it."""

    class RaisingPose:
        def estimate(self, frame, bbox):
            raise RuntimeError("pose model exploded")

    class RaisingFace:
        def estimate(self, frame, kps):
            raise RuntimeError("face model exploded")

    async def go():
        pipe = Pipeline(cfg(), components=parts(pose=RaisingPose(), face=RaisingFace()))
        col = Collector()
        task = asyncio.create_task(pipe.run(col.on_frame, col.on_audio, col.on_rules))
        await feed_phone(pipe, 1.0)
        pipe.stop()
        await asyncio.wait_for(task, 5)
        return col

    col = asyncio.run(go())
    assert len(col.frames) >= 5 and len(col.rules) == len(col.frames)  # frames aren't dropped
    assert all(f.dog_detected and f.bbox is not None for f in col.frames)  # detection survives
    assert all(f.features.in_feeding_zone is True for f in col.frames)  # zone comes from detection, not pose
    pose_derived = ("tail_height", "tail_wag_hz", "mouth_open", "body_lowering", "motion_energy")
    assert all(getattr(f.features, k) is None for f in col.frames for k in pose_derived)
    assert all(f.features.ear_position == "unknown" for f in col.frames)  # its own "no data" sentinel, not None
    assert all(set(r.scores) == set(EMOTIONS) for r in col.rules)  # rules still runs on the degraded event


def test_stop_is_idempotent_and_ends_run_and_all_threads():
    async def go():
        pipe = Pipeline(cfg(), components=parts())
        col = Collector()
        task = asyncio.create_task(pipe.run(col.on_frame, col.on_audio, col.on_rules))
        await asyncio.sleep(0.2)
        pipe.stop()
        pipe.stop()
        await asyncio.wait_for(task, 5)
        return pipe

    pipe = asyncio.run(go())
    assert pipe.status()["state"] == "stopped"
    assert not any(t.is_alive() for t in pipe._threads)
    pipe.stop()  # and again after it's over


def test_stop_before_run_makes_run_a_noop_and_run_twice_is_an_error():
    async def go():
        pipe = Pipeline(cfg(), components=parts())
        pipe.stop()
        await pipe.run(lambda e: None, lambda e: None, lambda e: None)  # returns immediately

        pipe2 = Pipeline(cfg(), components=parts())
        t = asyncio.create_task(pipe2.run(lambda e: None, lambda e: None, lambda e: None))
        await asyncio.sleep(0.1)
        with pytest.raises(RuntimeError):
            await pipe2.run(lambda e: None, lambda e: None, lambda e: None)
        pipe2.stop()
        await asyncio.wait_for(t, 5)

    asyncio.run(go())


def test_a_raising_callback_does_not_stop_the_pipeline(caplog):
    async def go():
        pipe = Pipeline(cfg(), components=parts())
        col = Collector()

        def bad(e):
            raise ValueError("boom")

        task = asyncio.create_task(pipe.run(bad, col.on_audio, col.on_rules))
        await feed_phone(pipe, 1.0)
        pipe.stop()
        await asyncio.wait_for(task, 5)
        return col

    with caplog.at_level(logging.ERROR):
        col = asyncio.run(go())
    assert len(col.rules) >= 4  # rules kept flowing
    assert sum("callback raised" in r.getMessage() for r in caplog.records) == 1  # rate-limited


def test_slow_callbacks_never_hold_up_the_video_thread(monkeypatch):
    monkeypatch.setattr(P, "OUTBOX_MAX", 6)

    async def go():
        pipe = Pipeline(cfg(), components=parts())
        seen = []

        def slow(e):
            seen.append(e)
            time.sleep(0.4)  # blocks the loop: the workers must carry on regardless

        task = asyncio.create_task(pipe.run(slow, lambda e: None, lambda e: None))
        # feed from a thread: the loop is busy inside `slow`
        stop = threading.Event()

        def phone():
            while not stop.is_set():
                pipe.ingest_frame(jpeg(), time.time())
                time.sleep(0.1)

        th = threading.Thread(target=phone)
        th.start()
        await asyncio.sleep(3.0)
        stop.set()
        th.join()
        processed = len(pipe._frame_times)
        pipe.stop()
        await asyncio.wait_for(task, 10)
        return pipe, seen, processed

    pipe, seen, processed = asyncio.run(go())
    assert processed > len(seen) + 5  # the video thread ran at full rate while the loop was stuck
    assert pipe._outbox_dropped > 0  # ...and the oldest events were dropped, not queued forever


# -- ingest on other sources -------------------------------------------------------------------


def test_ingest_on_a_non_browser_source_is_one_warning_and_a_noop(caplog):
    pipe = Pipeline(cfg(type="webcam"), components={**parts(), "source": MediaSources(type="webcam")})
    with caplog.at_level(logging.WARNING):
        for _ in range(50):
            pipe.ingest_frame(jpeg(), time.time())
            pipe.ingest_audio(b"\x00\x00" * 100, 48_000, time.time())
    assert len(caplog.records) == 1
    assert pipe.latest_frame_jpeg() is None
    pipe.stop()


def test_ingest_never_raises_on_garbage():
    pipe = Pipeline(cfg(), components=parts())
    for bad in (None, b"", b"junk", 12, object()):
        pipe.ingest_frame(bad, time.time())
        pipe.ingest_audio(bad, 48_000, time.time())
    pipe.ingest_audio(b"\x00\x00", "nope", "nope")
    pipe.stop()


def test_ingest_after_stop_is_ignored():
    pipe = Pipeline(cfg(), components=parts())
    pipe.stop()
    pipe.ingest_frame(jpeg(), time.time())
    pipe.ingest_audio(b"\x00\x00", 48_000, time.time())


# -- treats and fast file mode -----------------------------------------------------------------

needs_ffmpeg = pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg not installed")


@pytest.fixture(scope="module")
def clip(tmp_path_factory):
    path = tmp_path_factory.mktemp("clips") / "clip.mp4"
    subprocess.run(
        ["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "testsrc=duration=4:size=160x120:rate=25",
         "-f", "lavfi", "-i", "sine=frequency=440:duration=4", "-shortest", "-pix_fmt", "yuv420p", str(path)],
        check=True,
    )
    return path


def run_fast(clip, treat_at=None, **c):
    """Batch-run the clip; returns (frames, audio, rules) in callback order."""
    async def go():
        pipe = Pipeline(cfg(type="file", path=str(clip), fast=True, **c), components=parts())
        if treat_at is not None:
            pipe.mark_treat(pipe.clock_start() + treat_at)
        col = Collector()
        await asyncio.wait_for(pipe.run(col.on_frame, col.on_audio, col.on_rules), 30)
        return col

    return asyncio.run(go())


@needs_ffmpeg
def test_fast_mode_is_complete_ordered_and_merges_audio_by_timestamp(clip):
    col = run_fast(clip)
    assert 38 <= len(col.frames) <= 42 and len(col.rules) == len(col.frames)  # 4 s at 10 fps, nothing dropped
    assert all(f.source == "file" for f in col.frames)
    assert len(col.audio) >= 2

    # Replay in the order they were delivered: audio events must sit among the frames by timestamp.
    async def order():
        pipe = Pipeline(cfg(type="file", path=str(clip), fast=True), components=parts())
        seq = []
        await pipe.run(lambda e: seq.append(("f", e.ts)), lambda e: seq.append(("a", e.ts)), lambda e: None)
        return seq

    seq = asyncio.run(order())
    frame_ts = [ts for k, ts in seq if k == "f"]
    assert frame_ts == sorted(frame_ts)
    for i, (kind, ts) in enumerate(seq):
        if kind == "a":
            later = [t for k, t in seq[:i] if k == "f" and t > ts + 1e-6]
            assert not later, "audio delivered after a frame from its future"


@needs_ffmpeg
def test_a_treat_raises_the_excited_score_for_exactly_the_treat_window(clip):
    base = run_fast(clip)
    treated = run_fast(clip, treat_at=1.0)  # treat_window_s is 1.0 in the test config
    t0 = treated.frames[0].ts  # each run has its own wall-clock origin; clip time is relative to it
    assert len(base.rules) == len(treated.rules)
    diffs = [(t.ts - t0, t.scores["excited"] - b.scores["excited"]) for b, t in zip(base.rules, treated.rules)]
    inside = [d for s, d in diffs if 1.0 <= s <= 2.0]
    outside = [d for s, d in diffs if s < 0.95 or s > 2.05]
    assert inside and all(d >= 0.2 for d in inside), [(round(s, 2), round(d, 3)) for s, d in diffs if abs(d) > 1e-9]  # config event_bonus.excited.treat is 0.25
    assert outside and all(abs(d) < 1e-9 for d in outside), [(round(s, 2), round(d, 3)) for s, d in diffs if abs(d) > 1e-9]


def test_treat_window_semantics():
    pipe = Pipeline(cfg(), components=parts())
    pipe.mark_treat(100.0)
    assert not pipe._treat_recent(99.9)
    assert pipe._treat_recent(100.0) and pipe._treat_recent(101.0)
    assert not pipe._treat_recent(101.1)
    pipe.mark_treat(200.0)
    assert pipe._treat_recent(200.5) and not pipe._treat_recent(150.0)  # several treats coexist
    pipe.stop()


@needs_ffmpeg
def test_status_of_a_file_pipeline_and_source_labels(clip):
    async def go():
        pipe = Pipeline(cfg(type="file", path=str(clip), fast=True), components=parts())
        assert pipe.status() == {"source": "file", "state": "stopped", "fps": 0.0, "last_frame_age_s": None, "audio_ok": False}
        await pipe.run(lambda e: None, lambda e: None, lambda e: None)
        return pipe.status()

    st = asyncio.run(go())
    assert st["state"] == "stopped" and st["source"] == "file"


def test_a_dying_video_source_surfaces_from_run():
    class Broken:
        type, fast = "file", False

        def video(self):
            raise RuntimeError("could not open video source")
            yield  # pragma: no cover

        def audio(self):
            return iter(())

        def close(self):
            pass

        def push_frame(self, *a):
            pass

    async def go():
        pipe = Pipeline(cfg(type="file"), components={**parts(), "source": Broken()})
        await asyncio.wait_for(pipe.run(lambda e: None, lambda e: None, lambda e: None), 5)

    with pytest.raises(RuntimeError, match="could not open"):
        asyncio.run(go())


def test_debug_overlay_draws_on_portrait_frames():
    import sys
    from pathlib import Path

    sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))
    from run_pipeline import draw_overlay

    ev = FrameEvent(ts=1.0, source="file", dog_detected=True, bbox=(40, 200, 300, 500),
                    body_keypoints={"withers": (150.0, 300.0, 0.9), "hip": None})
    label = RulesLabel(ts=1.0, emotion="excited", confidence=0.6, scores={e: 0.1 for e in EMOTIONS})
    frame = np.zeros((640, 360, 3), np.uint8)
    out = draw_overlay(frame, ev, label, [AudioEvent(ts=0.5, label="yip", score=0.7)], CFG["data"]["feeding_zone"], True)
    assert out.shape == frame.shape and out.any() and not frame.any()  # drawn on a copy
