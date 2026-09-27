from pathlib import Path

import numpy as np
import pytest

from backend.audio import yamnet_events as Y
from backend.audio.yamnet_events import SR, WINDOW, AudioEventDetector

MODEL_DIR = Path(__file__).resolve().parents[2] / "models" / "yamnet"
CFG = {"data": {"audio": {"model_dir": str(MODEL_DIR)}}}
needs_model = pytest.mark.skipif(not (MODEL_DIR / "saved_model.pb").exists(), reason="YAMNet not downloaded")


class ScriptedDetector(AudioEventDetector):
    """Real windowing and debounce, but the classifier returns a scripted label per window."""

    def __init__(self, labels, debounce_s=1.0):
        self.threshold, self.silence_rms, self.debounce_s = 0.3, 0.005, debounce_s
        self.max_gap_s, self.hop = 0.5, SR // 2
        self._script = iter(labels)
        self.reset()

    def _classify(self, window):
        return next(self._script), 0.8


def feed(det, seconds, ts0=1000.0, chunk_s=0.25):
    audio = np.zeros(int(seconds * SR), dtype=np.float32)
    step = int(chunk_s * SR)
    out = []
    for i in range(0, len(audio), step):
        out += det.push(ts0 + i / SR, audio[i : i + step])
    return out


def test_windows_are_emitted_at_the_hop_and_stamped_at_window_end():
    det = ScriptedDetector(["bark"] * 10, debounce_s=0)
    events = feed(det, 3.0)
    assert len(events) == 1 + int((3 * SR - WINDOW) // det.hop)
    assert events[0].ts == pytest.approx(1000.0 + WINDOW / SR)
    assert events[1].ts - events[0].ts == pytest.approx(0.5)


def test_one_bark_across_overlapping_windows_gives_one_event():
    det = ScriptedDetector(["silence", "bark", "bark", "silence", "silence"])
    events = feed(det, 3.0)
    assert [e.label for e in events] == ["silence", "bark", "silence"]


def test_repeated_bark_after_debounce_is_a_new_event():
    det = ScriptedDetector(["bark"] * 10, debounce_s=0.9)
    events = feed(det, 4.0)  # 7 windows, 0.5 s apart: emitted at windows 0, 2, 4, 6
    assert len(events) == 4


def test_different_dog_labels_do_not_debounce_each_other():
    det = ScriptedDetector(["bark", "yip", "silence", "silence"])
    assert [e.label for e in feed(det, 2.5)] == ["bark", "yip", "silence"]


def test_timestamp_gap_restarts_the_buffer():
    det = ScriptedDetector(["bark"] * 5, debounce_s=0)
    det.push(0.0, np.zeros(SR // 2, dtype=np.float32))
    events = det.push(10.0, np.zeros(SR // 2, dtype=np.float32))  # half a window before + half after: no stitching
    assert events == []


@needs_model
def test_silence_and_noise_with_the_real_model():
    det = AudioEventDetector(CFG)
    events = feed(det, 2.0)
    assert events and {e.label for e in events} == {"silence"}
    rng = np.random.default_rng(0)
    noise = (rng.standard_normal(3 * SR) * 0.1).astype(np.float32)
    det.reset()
    labels = {e.label for i in range(0, len(noise), SR // 4) for e in det.push(i / SR, noise[i : i + SR // 4])}
    assert labels and "bark" not in labels and "silence" not in labels


@needs_model
def test_label_map_rejects_unknown_class_names():
    bad = {"data": {"audio": {"model_dir": str(MODEL_DIR), "label_map": {"bark": ["Not a class"]}}}}
    with pytest.raises(ValueError, match="unknown AudioSet classes"):
        AudioEventDetector(bad)


@needs_model
def test_esc50_dog_clip_gives_bark():
    clips = sorted((Path(__file__).resolve().parents[2] / "data" / "esc50").glob("*-0.wav"))
    if not clips:
        pytest.skip("ESC-50 dog clips not downloaded (run scripts/eval_esc50.py)")
    from backend.sources import _decode_audio

    det = AudioEventDetector(CFG)
    data = _decode_audio(clips[0], required=True)
    labels = {e.label for i in range(0, len(data), SR // 4) for e in det.push(i / SR, data[i : i + SR // 4])}
    assert "bark" in labels
