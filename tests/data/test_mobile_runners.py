from __future__ import annotations

import json
from pathlib import Path
from typing import Any
import numpy as np
import pytest

from backend.contracts import AudioEvent
from backend.vision.keypoint_map import CANONICAL_NAMES, Keypoint
from backend.vision.detect import BBox, Detection
from backend.vision.mobile_runners import (
    MobileDetector,
    MobilePose,
    MobileAudio,
    load_mobile_spec,
    letterbox,
    unletterbox,
    detector_input,
    pose_input,
)
from backend.vision.detect import DogDetector
from backend.vision.pose import PoseEstimator
from backend.audio.yamnet_events import AudioEventDetector

SPEC_PATH = Path("backend/vision/mobile_spec.json")


def test_spec_loading():
    assert SPEC_PATH.exists()
    spec = load_mobile_spec()
    assert spec["version"] == 1
    assert "detector" in spec
    assert "pose" in spec
    assert "face" in spec
    assert "audio" in spec
    assert "cadence" in spec
    assert spec["detector"]["dog_class_coco"] == 16
    assert spec["pose"]["input_size"] == 256
    assert spec["pose"]["simcc_split_ratio"] == 2.0


def test_letterbox_and_unletterbox():
    # 640x480 frame letterboxed to 320x320
    im = np.zeros((480, 640, 3), dtype=np.uint8)
    lb, scale, offset = letterbox(im, size=320, pad=114)
    assert lb.shape == (320, 320, 3)
    # 640 scaled to 320 -> scale = 0.5. Height 480 * 0.5 = 240. Pad dw=0, dh=80 (40 top, 40 bottom).
    assert scale == 0.5
    assert offset[0] == 0  # left pad
    assert offset[1] == 40  # top pad
    # Border pad pixels are 114
    assert np.all(lb[0, 0] == 114)

    # Box in letterboxed space [0, 40, 320, 280] -> original space [0, 0, 640, 480]
    box_lb = [0.0, 40.0, 320.0, 280.0]
    orig_box = unletterbox(box_lb, scale, offset)
    assert pytest.approx(orig_box[0], abs=1e-3) == 0.0
    assert pytest.approx(orig_box[1], abs=1e-3) == 0.0
    assert pytest.approx(orig_box[2], abs=1e-3) == 640.0
    assert pytest.approx(orig_box[3], abs=1e-3) == 480.0


def test_mobile_detector_raw_head_decode():
    spec = load_mobile_spec()

    # Stub runtime returning [1, 84, 2100]
    class FakeRawRuntime:
        def __call__(self, x: np.ndarray) -> list[np.ndarray]:
            out = np.zeros((1, 84, 2100), dtype=np.float32)
            # Anchor 10 has a dog detection (class 16 -> index 4 + 16 = 20)
            # cx=0.5, cy=0.5, w=0.25, h=0.25 (normalized to letterbox)
            out[0, 0, 10] = 0.5
            out[0, 1, 10] = 0.5
            out[0, 2, 10] = 0.25
            out[0, 3, 10] = 0.25
            out[0, 20, 10] = 0.85  # score
            return [out]

    detector = MobileDetector(spec=spec, runtime=FakeRawRuntime(), kind="raw_head")
    frame = np.zeros((480, 640, 3), dtype=np.uint8)
    boxes, scores, classes = detector.predict_raw(frame)

    assert len(boxes) == 1
    assert scores[0] == pytest.approx(0.85, abs=1e-3)
    assert classes[0] == 16
    # Box should be unletterboxed to original frame coords
    # cx=160, cy=160 in 320x320 letterbox -> cy in unpadded image is 160 - 40 = 120. Scale=0.5 -> cx=320, cy=240.
    # w=80 -> 160. h=80 -> 160.
    x1, y1, x2, y2 = boxes[0]
    assert pytest.approx(x1, abs=1.0) == 240.0
    assert pytest.approx(y1, abs=1.0) == 160.0
    assert pytest.approx(x2, abs=1.0) == 400.0
    assert pytest.approx(y2, abs=1.0) == 320.0


def test_mobile_detector_end2end_decode():
    spec = load_mobile_spec()

    # Stub runtime returning [1, 300, 6] = (x1, y1, x2, y2, conf, cls) in 320 letterbox coords
    class FakeEnd2endRuntime:
        def __call__(self, x: np.ndarray) -> list[np.ndarray]:
            out = np.zeros((1, 300, 6), dtype=np.float32)
            out[0, 0] = [120.0, 100.0, 200.0, 180.0, 0.9, 16.0]  # dog
            out[0, 1] = [50.0, 50.0, 100.0, 100.0, 0.8, 0.0]  # person (not dog)
            return [out]

    detector = MobileDetector(spec=spec, runtime=FakeEnd2endRuntime(), kind="end2end")
    frame = np.zeros((480, 640, 3), dtype=np.uint8)
    boxes, scores, classes = detector.predict_raw(frame)

    assert len(boxes) == 1
    assert scores[0] == pytest.approx(0.9, abs=1e-3)
    assert classes[0] == 16


def test_mobile_pose_simcc_decode():
    spec = load_mobile_spec()

    # SimCC head outputs: simcc_x [1, 17, 512], simcc_y [1, 17, 512]
    # For keypoint 0 (L_Eye -> canonical "left_eye"):
    # Peak at bin 200 in x, bin 150 in y.
    # argmax / 2.0 -> x=100.0, y=75.0 in 256x256 input space.
    class FakePoseRuntime:
        def __call__(self, x: np.ndarray) -> list[np.ndarray]:
            simcc_x = np.zeros((1, 17, 512), dtype=np.float32)
            simcc_y = np.zeros((1, 17, 512), dtype=np.float32)
            # Point 0: L_Eye (mapped to left_eye)
            simcc_x[0, 0, 200] = 0.9
            simcc_y[0, 0, 150] = 0.8
            # Point 1: R_Eye (sub-threshold)
            simcc_x[0, 1, 100] = 0.4
            simcc_y[0, 1, 100] = 0.4  # score 0.4*0.4 = 0.16 < 0.3
            return [simcc_x, simcc_y]

    pose = MobilePose(spec=spec, runtime=FakePoseRuntime())
    frame = np.zeros((480, 640, 3), dtype=np.uint8)
    bbox: BBox = (100.0, 100.0, 300.0, 300.0)

    kps = pose.estimate(frame, bbox)
    assert isinstance(kps, dict)
    assert set(kps.keys()) == set(CANONICAL_NAMES)

    # left_eye should be detected
    assert kps["left_eye"] is not None
    x, y, conf = kps["left_eye"]
    assert conf == pytest.approx(0.72, abs=1e-3)  # 0.9 * 0.8
    assert 0 <= x <= 640
    assert 0 <= y <= 480

    # right_eye is sub-threshold -> None
    assert kps["right_eye"] is None

    # unmapped points like tail_tip should be None
    assert kps["tail_tip"] is None


def test_mobile_audio_int16_and_scoring():
    spec = load_mobile_spec()

    # Stub runtime returning [1, 521]
    class FakeYamnetRuntime:
        def __call__(self, x: np.ndarray) -> list[np.ndarray]:
            assert x.shape == (15600,)
            assert x.dtype == np.float32
            out = np.zeros((1, 521), dtype=np.float32)
            # Bark class
            out[0, 70] = 0.85
            return [out]

    audio_runner = MobileAudio(spec=spec, runtime=FakeYamnetRuntime(), class_names=None)
    # Test pcm16 input conversion
    pcm16 = np.zeros(15600, dtype=np.int16)
    pcm16[100] = 16384
    scores = audio_runner(pcm16)
    assert scores.shape == (521,)
    assert scores[70] == pytest.approx(0.85, abs=1e-3)


def test_dog_detector_tflite_backend_integration():
    model_path = Path("out/android_models/det_yolo26n_320_int8.tflite")
    if not model_path.exists():
        pytest.skip(f"{model_path} not found")

    cfg = {
        "data": {
            "feeding_zone": [[0.2, 0.4], [0.8, 0.4], [0.8, 0.95], [0.2, 0.95]],
            "detect": {
                "backend": "tflite",
                "model": str(model_path),
                "conf": 0.25,
                "imgsz": 320,
                "ema_alpha": 0.5,
                "ema_reset_s": 1.0,
            }
        }
    }
    detector = DogDetector(cfg)
    frame = np.zeros((480, 640, 3), dtype=np.uint8)
    # Zero frame: no dog
    det = detector.detect(frame)
    assert det is None or isinstance(det, Detection)


def test_pose_estimator_tflite_backend_integration():
    model_path = Path("out/android_models/pose_rtmpose_ap10k_litert.tflite")
    if not model_path.exists():
        pytest.skip(f"{model_path} not found")

    cfg = {
        "data": {
            "keypoint_conf_threshold": 0.3,
            "pose": {
                "backend": "tflite",
                "model": str(model_path),
                "crop_pad": 0.15,
            }
        }
    }
    pose = PoseEstimator(cfg)
    frame = np.zeros((480, 640, 3), dtype=np.uint8)
    bbox: BBox = (50.0, 50.0, 300.0, 300.0)
    kps = pose.estimate(frame, bbox)
    assert isinstance(kps, dict)
    assert set(kps.keys()) == set(CANONICAL_NAMES)


def test_audio_event_detector_tflite_backend_integration():
    model_path = Path("out/android_models/audio_yamnet.tflite")
    if not model_path.exists():
        pytest.skip(f"{model_path} not found")

    cfg = {
        "data": {
            "audio": {
                "backend": "tflite",
                "model": str(model_path),
                "hop_s": 0.48,
                "threshold": 0.3,
                "silence_rms": 0.005,
                "debounce_s": 1.0,
            }
        }
    }
    det = AudioEventDetector(cfg)
    chunk = np.zeros(16000, dtype=np.float32)
    events = det.push(0.0, chunk)
    assert isinstance(events, list)
    for ev in events:
        assert isinstance(ev, AudioEvent)
