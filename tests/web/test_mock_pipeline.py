import asyncio
import inspect

import cv2
import numpy as np

from backend.contracts import AudioEvent, FrameEvent, RulesLabel
from backend.demo.mock_pipeline import LOOP_S, PHASES, MockPipeline
from backend.pipeline import PIPELINE_METHODS, Pipeline

T0 = 1_000_000.0


def _mp():
    return MockPipeline({}, clock=lambda: T0)


def test_same_interface_as_pipeline():
    for name in PIPELINE_METHODS:
        mine, theirs = inspect.signature(getattr(MockPipeline, name)), inspect.signature(getattr(Pipeline, name))
        assert list(mine.parameters) == list(theirs.parameters), name


def test_script_covers_90s_in_order():
    assert sum(d for _, d in PHASES) == LOOP_S == 90.0
    mp = _mp()
    seen = []
    for t in np.arange(0, LOOP_S, 0.5):
        p = mp.phase_at(T0 + t)
        if not seen or seen[-1] != p:
            seen.append(p)
    assert seen == ["relaxed", "excited", "happy", "disinterested", "anxious", "absent", "relaxed"]
    assert mp.phase_at(T0 + LOOP_S + 1) == "relaxed"  # loops


def test_step_produces_valid_contracts_matching_phase():
    mp = _mp()
    frame, audio, rules = mp.step(T0 + 15)  # excited
    assert isinstance(frame, FrameEvent) and isinstance(rules, RulesLabel)
    assert frame.dog_detected and rules.emotion == "excited"
    assert all(isinstance(a, AudioEvent) for a in audio)
    FrameEvent.model_validate_json(frame.to_json())
    absent, _, r2 = mp.step(T0 + 68)  # absent phase: 66-72 s
    assert not absent.dog_detected and r2.emotion == "unknown"


def test_excited_phase_has_yips_and_fast_wag():
    mp = _mp()
    labels, wags = [], []
    for t in np.arange(12.0, 24.0, 0.125):
        f, a, _ = mp.step(T0 + t)
        labels += [e.label for e in a]
        if f.features.tail_wag_hz is not None:
            wags.append(f.features.tail_wag_hz)
    assert "yip" in labels and np.median(wags) > 3.0


def test_occasional_null_features_but_not_all():
    mp = _mp()
    feats = [mp.step(T0 + t)[0].features for t in np.arange(0, 12, 0.125)]
    nulls = sum(f.mouth_open is None for f in feats)
    assert 0 < nulls < len(feats) / 2


def test_mark_treat_jumps_to_excited():
    mp = _mp()
    assert mp.phase_at(T0 + 50) == "disinterested"
    mp.mark_treat(T0 + 50)
    assert mp.phase_at(T0 + 50.1) == "excited"


def test_latest_frame_is_jpeg_and_ingested_frame_wins():
    mp = _mp()
    assert mp.latest_frame_jpeg() is None
    mp.step(T0 + 1)
    img = cv2.imdecode(np.frombuffer(mp.latest_frame_jpeg(), np.uint8), cv2.IMREAD_COLOR)
    assert img is not None and img.shape[2] == 3
    ok, phone = cv2.imencode(".jpg", np.zeros((640, 360, 3), np.uint8))
    mp.ingest_frame(phone.tobytes(), T0 + 1.1)
    mp.step(T0 + 1.2)
    assert mp.latest_frame_jpeg() == phone.tobytes()
    assert mp.status()["source"] == "browser"


def test_status_and_ingest_never_raise():
    mp = _mp()
    mp.ingest_audio(b"\x00\x01" * 800, 48000, T0)
    mp.ingest_frame(b"not a jpeg", T0)
    s = mp.status()
    assert set(s) == {"source", "state", "fps", "last_frame_age_s", "audio_ok"}


async def test_run_calls_callbacks_then_stops():
    mp = MockPipeline({"data": {"fps": 20}})
    frames, rules = [], []
    task = asyncio.create_task(mp.run(frames.append, lambda a: None, rules.append))
    await asyncio.sleep(0.3)
    mp.stop()
    await asyncio.wait_for(task, 1.0)
    assert len(frames) >= 3 and len(rules) == len(frames)
    assert mp.status()["state"] == "stopped"
