"""Tests for the ROI-motion tail-wag estimator (backend/vision/wag.py)."""

from __future__ import annotations

import cv2
import numpy as np
import pytest

from backend.vision.wag import (
    RoiWagEstimator,
    compute_roi_shift,
    estimate_wag_hz,
    extract_tail_roi,
)


def _generate_synthetic_roi_bar(
    f_hz: float,
    fps: float = 15.0,
    duration: float = 3.0,
    amplitude_px: float = 10.0,
    bar_width: int = 3,
    roi_size: int = 64,
) -> list[tuple[float, np.ndarray]]:
    """Generate 64x64 synthetic ROIs with a vertical bar oscillating at f_hz."""
    n = int(fps * duration)
    dt = 1.0 / fps
    frames = []
    center_x = (roi_size - 1) / 2.0
    for i in range(n):
        t = i * dt
        roi = np.full((roi_size, roi_size), 50, dtype=np.uint8)
        x_bar = int(round(center_x + amplitude_px * np.sin(2.0 * np.pi * f_hz * t)))
        x_min = max(0, x_bar - bar_width // 2)
        x_max = min(roi_size, x_bar + bar_width // 2 + 1)
        roi[10:54, x_min:x_max] = 200
        frames.append((t, roi))
    return frames


@pytest.mark.parametrize("f_hz", [1.0, 2.0, 3.0, 4.0, 6.0])
def test_synthetic_roi_oscillating_bar_frequencies(f_hz: float):
    """A synthetic 64x64 ROI with a bar oscillating at f in {1, 2, 3, 4, 6} Hz,
    sampled at 15 fps for 3 s, gives an estimate within +-0.3 Hz.
    """
    estimator = RoiWagEstimator(
        min_hz=0.8,
        max_hz=7.5,
        min_samples=10,
        min_span_s=1.5,
        wag_min_peak_ratio=0.3,
    )
    frames = _generate_synthetic_roi_bar(f_hz=f_hz, fps=15.0, duration=3.0)
    est = None
    for t, roi in frames:
        est = estimator.push(roi, t)

    assert est is not None, f"Expected estimate for {f_hz} Hz, got None"
    assert abs(est - f_hz) <= 0.3, f"Expected {f_hz} +- 0.3 Hz, got {est:.3f} Hz"


def test_still_roi_with_noise_returns_zero():
    """A still ROI with noise returns 0.0."""
    estimator = RoiWagEstimator(
        min_hz=1.0,
        max_hz=8.0,
        min_samples=10,
        min_span_s=1.5,
        wag_min_peak_ratio=0.3,
    )
    fps = 15.0
    duration = 3.0
    n = int(fps * duration)
    dt = 1.0 / fps
    rng = np.random.default_rng(42)

    est = None
    for i in range(n):
        t = i * dt
        roi = np.full((64, 64), 60, dtype=np.uint8)
        roi[10:54, 30:34] = 200  # stationary bar
        noise = rng.normal(0, 8, (64, 64)).astype(np.int16)
        noisy_roi = np.clip(roi.astype(np.int16) + noise, 0, 255).astype(np.uint8)
        est = estimator.push(noisy_roi, t)

    assert est == 0.0, f"Expected still ROI with noise to return 0.0, got {est}"


def test_whole_body_translation_suppressed_by_box_relative_roi():
    """Whole-body translation (the dog walking) suppresses the wag,
    because the ROI signal is computed relative to the box.
    """
    estimator = RoiWagEstimator(
        min_hz=1.0,
        max_hz=8.0,
        min_samples=10,
        min_span_s=1.5,
    )
    fps = 15.0
    duration = 3.0
    n = int(fps * duration)
    dt = 1.0 / fps
    v_x = 40.0  # dog walking at 40 px/s

    rng = np.random.default_rng(42)
    bg = rng.normal(70, 10, (240, 480)).astype(np.uint8)

    est = None
    for i in range(n):
        t = i * dt
        frame = bg.copy()
        x_dog = int(round(50.0 + v_x * t))
        # Dog body 100 px wide, 70 px high
        frame[80:150, x_dog : x_dog + 100] = 170
        # Stationary tail relative to body at the rear (left side):
        frame[85:120, x_dog : x_dog + 8] = 220

        # Bounding box tracks the dog:
        bbox = (float(x_dog), 80.0, float(x_dog + 100), 150.0)
        # Tail base keypoint at (x_dog + 5, 88.0)
        kps = {"tail_base": (float(x_dog + 5), 88.0, 0.95), "nose": (float(x_dog + 95), 100.0, 0.95)}

        # Extract box-relative ROI
        roi = extract_tail_roi(frame, bbox=bbox, kps=kps, ts=t, last_pose_ts=t)
        assert roi is not None
        est = estimator.push(roi, t)

    assert est == 0.0, f"Expected walking dog without wag to return 0.0, got {est}"


def test_extract_tail_roi_with_pose_keypoints():
    """ROI is extracted centered around tail_base when available and fresh."""
    frame = np.zeros((200, 300), dtype=np.uint8)
    bbox = (50.0, 40.0, 250.0, 160.0)
    kps = {"tail_base": (70.0, 60.0, 0.9), "nose": (230.0, 80.0, 0.9)}

    roi = extract_tail_roi(frame, bbox=bbox, kps=kps, ts=1.0, last_pose_ts=0.5, target_size=(64, 64))
    assert roi is not None
    assert roi.shape == (64, 64)


def test_extract_tail_roi_fallback_rear_bbox():
    """ROI is extracted from rear 35% of bbox when tail_base is missing."""
    frame = np.zeros((200, 300), dtype=np.uint8)
    bbox = (50.0, 40.0, 250.0, 160.0)  # width 200, height 120
    # Head on right (nose at x=230) -> rear should be left 35%
    kps = {"nose": (230.0, 80.0, 0.9)}

    roi = extract_tail_roi(frame, bbox=bbox, kps=kps, ts=1.0, last_pose_ts=None, target_size=(64, 64))
    assert roi is not None
    assert roi.shape == (64, 64)


def test_wag_source_config_options():
    """FeatureExtractor respects wag_source configuration: pose, roi, both."""
    from backend.vision.detect import Detection
    from backend.vision.features import FeatureExtractor

    d = Detection(bbox=(50.0, 40.0, 250.0, 160.0), conf=0.9, in_feeding_zone=False, raw_bbox=(50.0, 40.0, 250.0, 160.0))

    # 1. wag_source == 'pose' (default)
    fx_pose = FeatureExtractor({"data": {"features": {"wag_source": "pose"}}})
    ev1 = fx_pose.update(1.0, d, {}, None, roi_wag_hz=3.5)
    # Pose keypoints missing -> pose wag is None
    assert ev1.features.tail_wag_hz is None

    # 2. wag_source == 'roi'
    fx_roi = FeatureExtractor({"data": {"features": {"wag_source": "roi"}}})
    ev2 = fx_roi.update(1.0, d, {}, None, roi_wag_hz=3.5)
    assert ev2.features.tail_wag_hz == 3.5

    # 3. wag_source == 'both'
    fx_both = FeatureExtractor({"data": {"features": {"wag_source": "both"}}})
    ev3 = fx_both.update(1.0, d, {}, None, roi_wag_hz=3.5)
    assert ev3.features.tail_wag_hz == 3.5
    assert fx_both.last_wag["roi"] == 3.5

