"""ROI-motion tail-wag estimator.

Estimates tail wag frequency (Hz) directly from camera-rate grayscale region-of-interest (ROI)
motion without requiring high-rate keypoint pose estimation.

The tail ROI is extracted around tail_base (from the last pose <= 1s old) or the rear 35% of the
dog's bounding box. For each frame, the signed horizontal intensity centroid shift of |frame_t - frame_{t-1}|
is computed inside the ROI, compensating for whole-body translation (box velocity).
The dominant oscillation frequency is estimated via Hann-windowed FFT with parabolic peak interpolation
over a 1-8 Hz band with an amplitude gate.
"""

from __future__ import annotations

import math
from collections import deque
from typing import Any, Sequence

import cv2
import numpy as np

BBox = tuple[float, float, float, float]


def estimate_wag_hz(
    t: Sequence[float] | np.ndarray,
    signal: Sequence[float] | np.ndarray,
    min_hz: float = 1.0,
    max_hz: float = 8.0,
    min_std: float = 0.015,
    min_peak_ratio: float = 0.3,
    min_samples: int = 10,
    min_span_s: float = 1.5,
    max_gap_s: float = 0.75,
) -> float | None:
    """Dominant frequency of an oscillation signal over the latest unbroken run.

    Uses Hann-windowed FFT, parabolic peak interpolation, and spectral peak-to-total ratio.
    Returns:
      - None if too few samples or insufficient time span
      - 0.0 if signal amplitude is below min_std or motion is not periodic (ratio < min_peak_ratio)
      - float >= min_hz if a clear periodic oscillation is detected
    """
    if len(t) < min_samples or len(signal) < min_samples:
        return None

    # Extract the latest unbroken run (gaps <= max_gap_s)
    run_t = [t[-1]]
    run_sig = [signal[-1]]
    for i in range(len(t) - 2, -1, -1):
        if run_t[-1] - t[i] > max_gap_s:
            break
        run_t.append(t[i])
        run_sig.append(signal[i])
    run_t.reverse()
    run_sig.reverse()

    if len(run_t) < min_samples or (run_t[-1] - run_t[0]) < min_span_s:
        return None

    t_arr = np.asarray(run_t, dtype=np.float64)
    sig_arr = np.asarray(run_sig, dtype=np.float64)

    # Amplitude gate
    if sig_arr.std() < min_std:
        return 0.0

    dt = float(np.median(np.diff(t_arr)))
    if dt <= 0:
        return None

    # Resample onto uniform grid
    grid = np.arange(t_arr[0], t_arr[-1] + 1e-9, dt)
    y = np.interp(grid, t_arr, sig_arr)

    # Detrend linear drift
    y = y - np.polyval(np.polyfit(grid - grid[0], y, 1), grid - grid[0])

    n = len(y)
    nfft = 1 << max(9, int(math.ceil(math.log2(n * 8))))
    power = np.abs(np.fft.rfft(y * np.hanning(n), nfft)) ** 2
    freqs = np.fft.rfftfreq(nfft, dt)

    hi = min(max_hz, 0.95 * 0.5 / dt)
    band = np.where((freqs >= min_hz) & (freqs <= hi))[0]
    if len(band) < 3:
        return None

    k = int(band[np.argmax(power[band])])

    # Periodicity gate: share of all (non-DC) power within +-0.25 Hz of the peak
    near = np.abs(freqs - freqs[k]) <= 0.25
    total = power[freqs >= 0.2].sum()
    if total <= 0 or (power[near].sum() / total) < min_peak_ratio:
        return 0.0

    # Parabolic log-interpolation around peak
    f = float(freqs[k])
    if 0 < k < len(power) - 1:
        a = np.log(power[k - 1] + 1e-12)
        b = np.log(power[k] + 1e-12)
        c = np.log(power[k + 1] + 1e-12)
        denom = a - 2 * b + c
        if denom < 0:
            f += 0.5 * (a - c) / denom * (freqs[1] - freqs[0])

    return f if f >= min_hz else 0.0


def compute_roi_shift(
    curr_roi: np.ndarray,
    prev_roi: np.ndarray,
    box_dx: float = 0.0,
) -> tuple[float, float]:
    """Compute the signed horizontal intensity centroid shift of |curr - prev|.

    Parameters:
      curr_roi: 2D uint8 grayscale image
      prev_roi: 2D uint8 grayscale image
      box_dx: Horizontal displacement of the bounding box between frames (pixels)

    Returns:
      (shift_norm, total_motion):
        shift_norm: centroid offset relative to ROI center, normalized by ROI width [-0.5, 0.5]
        total_motion: total pixel intensity difference (0.0 if still)
    """
    diff = cv2.absdiff(curr_roi, prev_roi).astype(np.float32)
    m00 = float(diff.sum())
    if m00 < 1e-3:
        return 0.0, 0.0

    w = curr_roi.shape[1]
    xs = np.arange(w, dtype=np.float32)
    m10 = float((diff.sum(axis=0) * xs).sum())
    centroid_x = m10 / m00
    center_x = (w - 1) / 2.0

    shift_px = centroid_x - center_x - box_dx
    shift_norm = float(shift_px / w)
    return shift_norm, m00


def extract_tail_roi(
    frame: np.ndarray,
    bbox: BBox | None,
    kps: dict[str, Any] | None = None,
    last_pose_ts: float | None = None,
    ts: float | None = None,
    target_size: tuple[int, int] = (64, 64),
) -> np.ndarray | None:
    """Extract a 64x64 grayscale tail ROI from the camera frame.

    Uses tail_base if pose is fresh (<= 1.0 s old), else falls back to the rear 35% of the bbox
    on the side opposite the head.
    """
    if bbox is None:
        return None

    if len(frame.shape) == 3:
        gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
    else:
        gray = frame

    h, w = gray.shape[:2]
    bx1, by1, bx2, by2 = bbox
    bw = max(1.0, bx2 - bx1)
    bh = max(1.0, by2 - by1)

    pose_fresh = (
        kps is not None
        and kps.get("tail_base") is not None
        and (last_pose_ts is None or ts is None or (ts - last_pose_ts) <= 1.0)
    )

    if pose_fresh:
        tb = kps["tail_base"]
        tb_x, tb_y = float(tb[0]), float(tb[1])
        dim = max(32, int(0.35 * max(bw, bh)))
        half = dim // 2
        rx1 = max(0, int(round(tb_x - half)))
        ry1 = max(0, int(round(tb_y - half)))
        rx2 = min(w, rx1 + dim)
        ry2 = min(h, ry1 + dim)
        rx1 = max(0, rx2 - dim)
        ry1 = max(0, ry2 - dim)
    else:
        # Fallback: rear 35% of the bbox away from head
        head_x = None
        if kps is not None:
            head_pts = [kps[k][0] for k in ("nose", "left_eye", "right_eye", "withers") if kps.get(k) is not None]
            if head_pts:
                head_x = float(np.mean(head_pts))

        box_mid = (bx1 + bx2) / 2.0
        rear_on_left = head_x >= box_mid if head_x is not None else True

        if rear_on_left:
            rx1 = int(bx1)
            rx2 = int(bx1 + 0.35 * bw)
        else:
            rx1 = int(bx2 - 0.35 * bw)
            rx2 = int(bx2)
        ry1 = int(by1)
        ry2 = int(by1 + 0.6 * bh)

        rx1, ry1 = max(0, rx1), max(0, ry1)
        rx2, ry2 = min(w, max(rx1 + 4, rx2)), min(h, max(ry1 + 4, ry2))

    crop = gray[ry1:ry2, rx1:rx2]
    if crop.size == 0 or crop.shape[0] < 2 or crop.shape[1] < 2:
        return None

    if crop.shape != target_size:
        crop = cv2.resize(crop, target_size, interpolation=cv2.INTER_LINEAR)
    return crop


class RoiWagEstimator:
    """Tracks ROI motion shifts at camera rate and estimates tail-wag frequency."""

    def __init__(
        self,
        min_hz: float = 1.0,
        max_hz: float = 8.0,
        min_std: float = 0.015,
        wag_min_peak_ratio: float = 0.3,
        window_s: float = 3.0,
        min_samples: int = 10,
        min_span_s: float = 1.5,
        max_gap_s: float = 0.75,
    ):
        self.min_hz = min_hz
        self.max_hz = max_hz
        self.min_std = min_std
        self.wag_min_peak_ratio = wag_min_peak_ratio
        self.window_s = window_s
        self.min_samples = min_samples
        self.min_span_s = min_span_s
        self.max_gap_s = max_gap_s

        self.history: deque[tuple[float, float]] = deque()
        self.prev_roi: np.ndarray | None = None
        self.prev_ts: float | None = None
        self.current_hz: float | None = None

    def reset(self) -> None:
        self.history.clear()
        self.prev_roi = None
        self.prev_ts = None
        self.current_hz = None

    def push(self, roi: np.ndarray, ts: float, box_dx: float = 0.0) -> float | None:
        """Push a grayscale ROI frame and return the updated wag frequency estimate."""
        if self.prev_roi is not None:
            shift, _ = compute_roi_shift(roi, self.prev_roi, box_dx=box_dx)
            self.history.append((ts, shift))

        self.prev_roi = roi
        self.prev_ts = ts

        # Evict old samples outside window_s
        while self.history and (ts - self.history[0][0]) > self.window_s:
            self.history.popleft()

        self.current_hz = self.estimate()
        return self.current_hz

    def estimate(self) -> float | None:
        """Estimate wag frequency from buffered shifts."""
        if len(self.history) < self.min_samples:
            return None

        t = [h[0] for h in self.history]
        sig = [h[1] for h in self.history]
        return estimate_wag_hz(
            t,
            sig,
            min_hz=self.min_hz,
            max_hz=self.max_hz,
            min_std=self.min_std,
            min_peak_ratio=self.wag_min_peak_ratio,
            min_samples=self.min_samples,
            min_span_s=self.min_span_s,
            max_gap_s=self.max_gap_s,
        )
