import logging
import os
import threading
import time
from pathlib import Path

import cv2
import numpy as np
import pytest

from backend import sources
from backend.contracts import FrameEvent
from backend.sources import AUDIO_SR, BROWSER_STALL_S, MediaSources

ROOT = Path(__file__).resolve().parents[2]


def jpeg(level: int, size: tuple[int, int] = (64, 48), quality: int = 90) -> bytes:
    """A flat grey JPEG (w, h) whose decoded mean identifies `level`."""
    img = np.full((size[1], size[0], 3), level, np.uint8)
    ok, buf = cv2.imencode(".jpg", img, [cv2.IMWRITE_JPEG_QUALITY, quality])
    assert ok
    return buf.tobytes()


def pcm(x: np.ndarray) -> bytes:
    return (np.clip(x, -1, 1) * 32767).astype("<i2").tobytes()


def collect(iterator, into: list):
    def run():
        for item in iterator:
            into.append(item)

    t = threading.Thread(target=run, daemon=True)
    t.start()
    return t


def push_tone(src, seconds, sr, freq=1000.0, chunk_s=0.1, t0=1000.0, jitter=0.0, seed=0):
    """Push a sine as a phone would: chunk i is stamped when its last sample was recorded (+ jitter)."""
    rng = np.random.default_rng(seed)
    n = round(chunk_s * sr)
    total = round(seconds * sr)
    for i in range(0, total - n + 1, n):
        t = (np.arange(i, i + n)) / sr
        ts = t0 + (i + n) / sr + rng.uniform(-jitter, jitter)
        src.push_audio(pcm(0.5 * np.sin(2 * np.pi * freq * t)), sr, ts)


# -- video -------------------------------------------------------------------------------------


def test_burst_of_frames_yields_only_the_newest():
    src = MediaSources(type="browser", fps=10)
    for i in range(100):
        src.push_frame(jpeg(i * 2), 1000.0 + i)
    got: list = []
    collect(src.video(), got)
    time.sleep(0.5)
    src.close()
    assert len(got) == 1
    ts, frame = got[0]
    assert ts == 1099.0 and frame.shape == (48, 64, 3)
    assert abs(float(frame.mean()) - 198) < 4


def test_frames_are_paced_to_the_target_fps():
    src = MediaSources(type="browser", fps=5)
    got: list = []
    collect(src.video(), got)
    end = time.monotonic() + 2.0
    i = 0
    while time.monotonic() < end:  # a "phone" sending 50 fps into a 5 fps pipeline
        src.push_frame(jpeg(i % 200), time.time())
        i += 1
        time.sleep(0.02)
    src.close()
    assert 8 <= len(got) <= 12
    assert all(time.time() - ts < 5 for ts, _ in got)
    ts = [t for t, _ in got]
    assert ts == sorted(ts) and 0.15 <= np.diff(ts).mean() <= 0.3


def test_push_is_cheap_and_never_decodes(monkeypatch):
    calls = []
    real = cv2.imdecode
    monkeypatch.setattr(sources.cv2, "imdecode", lambda *a, **k: calls.append(1) or real(*a, **k))
    src = MediaSources(type="browser", fps=10)
    data = jpeg(90, size=(640, 360))
    start = time.perf_counter()
    for i in range(1000):
        src.push_frame(data, float(i))
    assert time.perf_counter() - start < 0.25  # ~250 us each, far below any decode
    assert calls == []


@pytest.mark.parametrize("junk", [b"garbage", b"", None, 123, b"\xff\xd8" + os.urandom(400), jpeg(50)[:60]])
def test_malformed_frames_are_dropped_without_raising(junk, caplog):
    src = MediaSources(type="browser", fps=20)
    got: list = []
    collect(src.video(), got)
    with caplog.at_level(logging.WARNING):
        src.push_frame(junk, 1.0)
        time.sleep(0.2)
        assert got == [] or got[0][1].ndim == 3  # a truncated JPEG may legitimately decode partially
        n = len(got)
        src.push_frame(jpeg(120), 2.0)  # the consumer carries on afterwards
        time.sleep(0.3)
    src.close()
    assert len(got) == n + 1 and got[-1][0] == 2.0


def test_warnings_for_bad_input_are_rate_limited(caplog):
    src = MediaSources(type="browser", fps=10)
    with caplog.at_level(logging.WARNING):
        for _ in range(500):
            src.push_frame(b"garbage", 0.0)
            src.push_audio(b"\x00\x01\x02", 48_000, 0.0)  # odd length
    src.close()
    assert len(caplog.records) <= 4


def test_stall_triggers_and_clears_on_next_frame():
    assert BROWSER_STALL_S == 2.0
    src = MediaSources(type="browser", fps=10, stall_s=0.3)
    assert src.state() == "running" and src.last_frame_age_s() is None
    src.push_frame(jpeg(10), 1.0)
    assert src.state() == "running"
    time.sleep(0.45)
    assert src.state() == "stalled" and src.last_frame_age_s() > 0.3
    src.push_frame(jpeg(10), 2.0)
    assert src.state() == "running"
    src.close()
    assert src.state() == "stopped"


def test_never_connected_counts_as_stalled():
    src = MediaSources(type="browser", stall_s=0.2)
    time.sleep(0.3)
    assert src.state() == "stalled"
    src.close()


def test_stalled_source_recovers_through_the_iterator():
    src = MediaSources(type="browser", fps=20, stall_s=0.3)
    got: list = []
    collect(src.video(), got)
    src.push_frame(jpeg(10), 1.0)
    time.sleep(0.5)
    assert src.state() == "stalled" and len(got) == 1
    src.push_frame(jpeg(20), 2.0)
    time.sleep(0.15)
    assert src.state() == "running" and len(got) == 2
    src.close()


def test_close_stops_blocked_iterators_promptly():
    src = MediaSources(type="browser")
    v, a = [], []
    tv, ta = collect(src.video(), v), collect(src.audio(), a)
    time.sleep(0.2)
    src.close()
    tv.join(timeout=2.0)
    ta.join(timeout=2.0)
    assert not tv.is_alive() and not ta.is_alive()


def test_push_on_a_non_browser_source_is_a_warned_noop(caplog):
    src = MediaSources(type="webcam")
    with caplog.at_level(logging.WARNING):
        src.push_frame(jpeg(1), 0.0)
        src.push_audio(b"\x00\x00", 48_000, 0.0)
    assert len(caplog.records) == 1
    assert src.state() == "running"
    src.close()
    assert src.state() == "stopped"


def test_config_wiring():
    cfg = {"data": {"fps": 6, "source": {"type": "browser", "path": None, "device": 0, "audio": None,
                                          "loop": False, "stall_s": 1.5}}}
    src = MediaSources.from_config(cfg)
    assert src.type == "browser" and src.fps == 6 and src._in.stall_s == 1.5
    src.close()


# -- audio -------------------------------------------------------------------------------------


@pytest.mark.parametrize("jitter", [0.0, 0.03])
def test_48k_becomes_continuous_16k_chunks(jitter):
    src = MediaSources(type="browser", chunk_s=0.25)
    got: list = []
    t = collect(src.audio(), got)
    push_tone(src, 3.0, 48_000, t0=1000.0, jitter=jitter)
    time.sleep(0.5)
    src.close()
    t.join(timeout=2.0)

    assert 10 <= len(got) <= 12
    assert all(c.dtype == np.float32 and c.ndim == 1 and len(c) == 4000 for _, c in got)
    ts = np.array([t for t, _ in got])
    assert np.allclose(np.diff(ts), 0.25, atol=1e-9)  # jitter on arrival does not leak into timestamps
    assert abs(ts[0] - 1000.0) <= jitter + 1e-9

    # The samples are the right signal at the right time: no clicks at chunk seams, no phase drift.
    if jitter == 0.0:
        out = np.concatenate([c for _, c in got])
        tt = ts[0] + np.arange(len(out)) / AUDIO_SR
        expected = 0.5 * np.sin(2 * np.pi * 1000.0 * (tt - 1000.0))
        assert np.abs(out[100:] - expected[100:]).max() < 0.01


@pytest.mark.parametrize("sr", [8_000, 16_000, 22_050, 44_100, 48_000])
def test_any_rate_gives_16k_of_the_right_duration(sr):
    src = MediaSources(type="browser", chunk_s=0.25)
    got: list = []
    t = collect(src.audio(), got)
    push_tone(src, 2.5, sr, freq=500.0)
    time.sleep(0.4)
    src.close()
    t.join(timeout=2.0)
    total = sum(len(c) for _, c in got)
    assert 1.8 * AUDIO_SR <= total <= 2.5 * AUDIO_SR
    ts = [t for t, _ in got]
    ends = [t + len(c) / AUDIO_SR for t, c in got]
    assert np.allclose(ts[1:], ends[:-1], atol=1e-6)  # each chunk starts where the last ended


def test_odd_length_pcm_and_bad_rates_are_dropped_without_raising():
    src = MediaSources(type="browser")
    got: list = []
    collect(src.audio(), got)
    for bad, sr in [(b"\x01\x02\x03", 48_000), (b"", 48_000), (None, 48_000), (b"\x00\x00", 0), (b"\x00\x00", "x")]:
        src.push_audio(bad, sr, 1.0)
    assert len(src._in.audio) == 0
    push_tone(src, 1.0, 48_000)
    time.sleep(0.3)
    src.close()
    assert len(got) >= 2


def test_audio_ring_buffer_is_bounded_and_drops_the_oldest():
    src = MediaSources(type="browser")
    push_tone(src, 20.0, 48_000, t0=0.0)  # 20 s with nobody consuming
    assert src._in.audio_s <= 5.0 + 0.11 and len(src._in.audio) <= 51
    assert src._in.audio[0][0] > 14.0  # the oldest survivor is from the end of the stream
    src.close()


def test_a_gap_re_anchors_the_clock_and_restarts_the_stream():
    src = MediaSources(type="browser", chunk_s=0.25)
    got: list = []
    t = collect(src.audio(), got)
    push_tone(src, 1.0, 48_000, t0=1000.0)
    time.sleep(0.3)
    n_before = len(got)
    push_tone(src, 1.0, 48_000, t0=1010.0)  # the phone paused for ~9 s
    time.sleep(0.4)
    src.close()
    t.join(timeout=2.0)
    ts = [t for t, _ in got]
    assert n_before >= 2 and len(got) > n_before
    assert ts[n_before] == pytest.approx(1010.0, abs=0.02)  # anchored at the new chunk's start, not 1001
    assert ts == sorted(ts)


def test_audio_ok_follows_incoming_chunks():
    src = MediaSources(type="browser", stall_s=0.3)
    assert not src.audio_ok()
    src.push_audio(pcm(np.zeros(4800)), 48_000, 1.0)
    assert src.audio_ok()
    time.sleep(0.45)
    assert not src.audio_ok()
    src.close()


# -- portrait ----------------------------------------------------------------------------------


def test_zone_and_crop_helpers_work_on_portrait_frames():
    from backend.vision.detect import crop_padded, point_in_zone

    zone = [[0.2, 0.4], [0.8, 0.4], [0.8, 0.95], [0.2, 0.95]]
    shape = (640, 360, 3)  # h > w
    assert point_in_zone((180, 480), zone, shape)  # bottom-centre of a portrait frame
    assert not point_in_zone((180, 100), zone, shape)  # the top is outside the zone
    assert not point_in_zone((20, 480), zone, shape)  # the left margin is outside too
    crop, (ox, oy) = crop_padded(np.zeros(shape, np.uint8), (100, 300, 260, 600), 0.15)
    assert crop.shape[0] > crop.shape[1] * 1.5 and (ox, oy) == (76, 255)


def test_portrait_frames_give_valid_frame_events():
    from backend.vision.detect import Detection
    from backend.vision.features import FeatureExtractor
    from backend.vision.keypoint_map import CANONICAL_NAMES

    fx = FeatureExtractor({"data": {"keypoint_conf_threshold": 0.3, "features": {}}})
    bbox = (60.0, 300.0, 300.0, 600.0)  # tall frame, dog in the lower half
    det = Detection(bbox=bbox, conf=0.9, in_feeding_zone=True, raw_bbox=bbox)
    events = []
    for i in range(30):
        kps = {n: None for n in CANONICAL_NAMES}
        kps.update(withers=(220.0, 420.0, 0.9), hip=(80.0, 430.0 + i % 3, 0.9), tail_base=(80.0, 430.0, 0.9),
                   tail_tip=(40.0, 380.0, 0.9), nose=(280.0, 440.0, 0.9))
        events.append(fx.update(1000.0 + i / 8, det, kps, None))
    for e in events:
        FrameEvent.model_validate_json(e.to_json())
    assert all(e.dog_detected and e.features.in_feeding_zone for e in events)
    assert events[-1].features.tail_height is not None and events[-1].features.tail_height > 0  # tail up


REAL_MODELS = (ROOT / "models/yolo11s.pt").exists() and (ROOT / "models/dog_face_landmarks_full.tflite").exists()
CLIP = ROOT / "data/fallback/raw/german_shepherd_treat.webm"


@pytest.mark.skipif(not (REAL_MODELS and CLIP.exists()), reason="models or fallback clip not downloaded")
def test_portrait_clip_through_the_browser_path_and_real_models():
    """A portrait crop around the dog goes phone -> push_frame -> video() -> detect/pose/face/features."""
    import yaml

    from backend.vision.detect import DogDetector
    from backend.vision.face import FaceLandmarker
    from backend.vision.features import FeatureExtractor
    from backend.vision.pose import PoseEstimator

    cfg = yaml.safe_load((ROOT / "config.yaml").read_text())
    det = DogDetector(cfg)

    cap = cv2.VideoCapture(str(CLIP))
    cap.set(cv2.CAP_PROP_POS_FRAMES, 200)
    frames = []
    for _ in range(8):
        ok, f = cap.read()
        assert ok
        for _ in range(3):
            cap.grab()
        frames.append(f)
    cap.release()
    first = det.detect(frames[0])
    assert first is not None
    cx = int((first.bbox[0] + first.bbox[2]) / 2)
    side_h = frames[0].shape[0]
    w = int(side_h * 9 / 16)
    x0 = min(max(0, cx - w // 2), frames[0].shape[1] - w)
    portrait = [f[:, x0 : x0 + w] for f in frames]  # 9:16 window around the dog

    src = MediaSources(type="browser", fps=50)
    events = []
    det, pose, face, fx = DogDetector(cfg), PoseEstimator(cfg), FaceLandmarker(cfg), FeatureExtractor(cfg)
    it = src.video()
    for i, f in enumerate(portrait):
        ok, buf = cv2.imencode(".jpg", f, [cv2.IMWRITE_JPEG_QUALITY, 70])
        src.push_frame(buf.tobytes(), 1000.0 + i / 8)
        ts, frame = next(it)
        assert frame.shape[0] > frame.shape[1]  # still portrait after the trip
        d = det.detect(frame)
        kps = pose.estimate(frame, d.bbox) if d is not None else {}
        lms = face.estimate(frame, kps) if kps else None
        events.append(fx.update(ts, d, kps, lms))
    src.close()
    for e in events:
        FrameEvent.model_validate_json(e.to_json())
    seen = [e for e in events if e.dog_detected]
    assert len(seen) >= 6
    h, w = portrait[0].shape[:2]
    assert all(0 <= e.bbox[0] < e.bbox[2] <= w and 0 <= e.bbox[1] < e.bbox[3] <= h for e in seen)
