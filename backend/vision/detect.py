"""YOLO dog detection, feeding-zone test and crop helper.

    det = DogDetector(cfg)                    # cfg is the parsed config.yaml (reads `data.detect`)
    d = det.detect(frame)                     # Detection | None (best dog, smoothed bbox)
    crop, (ox, oy) = det.crop(frame, d.bbox)  # padded crop for the pose stage; (ox, oy) maps back

The feeding-zone test uses the bbox bottom-centre, not the centre. The zone is a patch of floor
seen by a static camera, and the bottom-centre is where the dog touches it. The bbox centre moves
when the dog lifts its head, sits up or stretches, which would flip in_feeding_zone with no change
in where the dog actually is.
"""

from __future__ import annotations

import argparse
import logging
import math
import time
from collections import deque
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import cv2
import numpy as np

logger = logging.getLogger(__name__)

BBox = tuple[float, float, float, float]


@dataclass(frozen=True)
class Detection:
    bbox: BBox  # smoothed (EMA), full-frame pixels, x1 y1 x2 y2
    conf: float
    in_feeding_zone: bool
    raw_bbox: BBox  # unsmoothed detector output


@dataclass(frozen=True)
class _BoxesAdapter:
    xyxy: np.ndarray
    conf: np.ndarray
    cls: np.ndarray


def point_in_zone(point: tuple[float, float], zone_norm: list[list[float]], frame_shape: tuple[int, ...]) -> bool:
    """Is a pixel point inside the polygon given in normalised (0..1) image coords?"""
    h, w = frame_shape[:2]
    poly = np.array([[x * w, y * h] for x, y in zone_norm], dtype=np.float32)
    return cv2.pointPolygonTest(poly, (float(point[0]), float(point[1])), False) >= 0


def crop_padded(frame: np.ndarray, bbox: BBox, pad: float) -> tuple[np.ndarray, tuple[int, int]]:
    """Crop the bbox padded by `pad` (fraction of its size per side), clipped to the frame.

    Returns (crop, (ox, oy)): add (ox, oy) to crop coords to get full-frame pixels.
    """
    h, w = frame.shape[:2]
    x1, y1, x2, y2 = bbox
    px, py = (x2 - x1) * pad, (y2 - y1) * pad
    cx1, cy1 = max(0, int(np.floor(x1 - px))), max(0, int(np.floor(y1 - py)))
    cx2, cy2 = min(w, int(np.ceil(x2 + px))), min(h, int(np.ceil(y2 + py)))
    return frame[cy1:cy2, cx1:cx2], (cx1, cy1)


def _iou(a: BBox, b: BBox) -> float:
    ix = max(0.0, min(a[2], b[2]) - max(a[0], b[0]))
    iy = max(0.0, min(a[3], b[3]) - max(a[1], b[1]))
    inter = ix * iy
    union = (a[2] - a[0]) * (a[3] - a[1]) + (b[2] - b[0]) * (b[3] - b[1]) - inter
    return inter / union if union > 0 else 0.0


def _to_np(x: Any) -> np.ndarray:
    return x.cpu().numpy() if hasattr(x, "cpu") else np.asarray(x)


def pick_device() -> str:
    import torch

    return "mps" if torch.backends.mps.is_available() else "cpu"


class DogDetector:
    def __init__(self, cfg: dict, model: Any = None, device: str | None = None):
        """`model` lets tests inject a fake with the ultralytics call/`names` interface."""
        data = cfg["data"]
        d = data.get("detect", {})
        self.conf = d.get("conf", 0.25)
        self.imgsz = d.get("imgsz", 640)
        self.alpha = float(d.get("ema_alpha", 0.5))  # weight of newest box at 8 fps
        if "ema_reset_s" in d:
            self.reset_after_s = float(d["ema_reset_s"])
        elif "ema_reset_misses" in d:
            self.reset_after_s = float(d["ema_reset_misses"]) / 8.0
            logger.info("Converted legacy detect.ema_reset_misses (%s) to detect.ema_reset_s (%.3f)",
                        d["ema_reset_misses"], self.reset_after_s)
        else:
            self.reset_after_s = 1.0  # default 8/8 fps = 1.0 s
        self.reset_after = d.get("ema_reset_misses", int(round(self.reset_after_s * 8.0)))

        if "ema_tau_s" in d:
            self.tau_s = float(d["ema_tau_s"])
        else:
            self.tau_s = -0.125 / math.log(1.0 - self.alpha) if 0.0 < self.alpha < 1.0 else 0.0

        self.pad = d.get("crop_pad", 0.15)
        self.anchor = d.get("zone_anchor", "bottom_center")
        if self.anchor not in ("bottom_center", "center"):
            raise ValueError(f"zone_anchor must be bottom_center or center, got {self.anchor!r}")
        self.zone = data["feeding_zone"]
        self.device = device or pick_device()

        self.backend = d.get("backend", "ultralytics")

        owns_model = model is None
        if owns_model:
            if self.backend in ("tflite", "onnx"):
                from backend.vision.mobile_runners import MobileDetector
                model_path = d.get("model")
                self.mobile_detector = MobileDetector(model_path=model_path)
                self.model = None
                self.dog_ids = [self.mobile_detector.dog_class]
            else:
                from ultralytics import YOLO
                from ultralytics.utils.downloads import attempt_download_asset

                path = Path(d.get("model", "models/yolo11s.pt"))
                path.parent.mkdir(parents=True, exist_ok=True)
                if not path.exists():
                    attempt_download_asset(path)  # one-off download; offline afterwards
                model = YOLO(str(path))
                self.model = model
                self.dog_ids = [int(i) for i, n in model.names.items() if n == "dog"]
        else:
            self.model = model
            self.dog_ids = [int(i) for i, n in model.names.items() if n == "dog"]
        if not self.dog_ids:
            raise ValueError("model has no 'dog' class")

        self._smoothed: BBox | None = None
        self._misses = 0
        self._last_ts: float | None = None
        self._last_dog_ts: float | None = None
        self.latencies_ms: deque[float] = deque(maxlen=200)
        self.first_call_ms: float | None = None  # new input shapes make MPS recompile; kept out of stats
        self._warmed = owns_model
        if owns_model:
            self._infer(np.zeros((self.imgsz, self.imgsz, 3), np.uint8))  # compile/warm the device

    # -- inference ---------------------------------------------------------------------------

    def _infer(self, frame: np.ndarray):
        if self.backend in ("tflite", "onnx"):
            boxes, scores, classes = self.mobile_detector.predict_raw(frame)
            return _BoxesAdapter(boxes, scores, classes)
        results = self.model(
            frame, classes=self.dog_ids, conf=self.conf, imgsz=self.imgsz, device=self.device, verbose=False
        )
        return results[0].boxes

    def detect(self, frame: np.ndarray, ts: float | None = None) -> Detection | None:
        """Best dog in the frame (highest confidence, ties broken by larger area), or None."""
        t = time.perf_counter()
        boxes = self._infer(frame)
        ms = (time.perf_counter() - t) * 1000
        if self.first_call_ms is None and self._warmed:
            self.first_call_ms = ms
        else:
            self.latencies_ms.append(ms)

        explicit_ts = ts is not None
        if ts is None:
            ts = 0.0 if self._last_ts is None else self._last_ts + 0.125
        dt = max(ts - self._last_ts, 0.0) if self._last_ts is not None else 0.125

        xyxy, conf, cls = _to_np(boxes.xyxy), _to_np(boxes.conf), _to_np(boxes.cls)
        keep = [i for i in range(len(conf)) if int(cls[i]) in self.dog_ids]
        if not keep:
            self._misses += 1
            if self._last_dog_ts is not None and (ts - self._last_dog_ts) >= self.reset_after_s:
                self._smoothed = None
            elif self._last_dog_ts is None and not explicit_ts and self._misses >= self.reset_after:
                self._smoothed = None
            self._last_ts = ts
            return None
        # Confidences within 0.005 count as equal, so two near-identical boxes resolve to the bigger one.
        best = max(keep, key=lambda i: (round(float(conf[i]), 2), (xyxy[i][2] - xyxy[i][0]) * (xyxy[i][3] - xyxy[i][1])))
        raw = tuple(float(v) for v in xyxy[best])
        if self._last_dog_ts is not None and (ts - self._last_dog_ts) >= self.reset_after_s:
            self._smoothed = None
        self._misses = 0
        self._last_dog_ts = ts
        bbox = self._smooth(raw, dt)
        self._last_ts = ts
        x1, y1, x2, y2 = bbox
        anchor = ((x1 + x2) / 2, y2 if self.anchor == "bottom_center" else (y1 + y2) / 2)
        return Detection(bbox, float(conf[best]), point_in_zone(anchor, self.zone, frame.shape), raw)

    def _smooth(self, raw: BBox, dt: float = 0.125) -> BBox:
        prev = self._smoothed
        if prev is None or _iou(prev, raw) < 0.1:  # first box, or a different dog/jump: don't drag
            self._smoothed = raw
        else:
            if self.tau_s > 0.0 and dt > 0.0:
                a = 1.0 - math.exp(-dt / self.tau_s)
            else:
                a = self.alpha
            self._smoothed = tuple(a * n + (1 - a) * o for n, o in zip(raw, prev))  # type: ignore[assignment]
        return self._smoothed

    # -- helpers -----------------------------------------------------------------------------

    def crop(self, frame: np.ndarray, bbox: BBox, pad: float | None = None) -> tuple[np.ndarray, tuple[int, int]]:
        """Padded crop of `bbox` (see `crop_padded`); `pad` defaults to the configured crop_pad."""
        return crop_padded(frame, bbox, self.pad if pad is None else pad)

    def latency_stats(self) -> dict[str, float]:
        """Mean and p95 detect() latency in ms over the last 200 calls."""
        if not self.latencies_ms:
            return {"mean_ms": 0.0, "p95_ms": 0.0, "n": 0}
        a = np.array(self.latencies_ms)
        return {"mean_ms": float(a.mean()), "p95_ms": float(np.percentile(a, 95)), "n": len(a)}


# -- CLI: annotated debug video ---------------------------------------------------------------


def draw_debug(frame: np.ndarray, det: Detection | None, zone: list[list[float]], latency_ms: float) -> np.ndarray:
    out = frame.copy()
    h, w = out.shape[:2]
    poly = np.array([[x * w, y * h] for x, y in zone], dtype=np.int32)
    in_zone = det is not None and det.in_feeding_zone
    zone_color = (0, 200, 0) if in_zone else (0, 0, 220)
    cv2.polylines(out, [poly], True, zone_color, 2)
    if det is not None:
        cv2.rectangle(out, tuple(int(v) for v in det.raw_bbox[:2]), tuple(int(v) for v in det.raw_bbox[2:]), (160, 160, 160), 1)
        p1, p2 = tuple(int(v) for v in det.bbox[:2]), tuple(int(v) for v in det.bbox[2:])
        cv2.rectangle(out, p1, p2, (255, 160, 0), 2)
        cv2.circle(out, (int((det.bbox[0] + det.bbox[2]) / 2), int(det.bbox[3])), 5, zone_color, -1)
        label = f"dog {det.conf:.2f} zone={'yes' if det.in_feeding_zone else 'no'}"
    else:
        label = "no dog"
    cv2.putText(out, f"{label}  {latency_ms:.0f} ms", (10, 28), cv2.FONT_HERSHEY_SIMPLEX, 0.8, (0, 255, 255), 2)
    return out


def main(argv: list[str] | None = None) -> None:
    import yaml

    from backend.sources import MediaSources
    from backend.vision.videoio import DebugVideoWriter

    p = argparse.ArgumentParser(description="Run dog detection on a clip and write an annotated video.")
    p.add_argument("--config", default="config.yaml")
    p.add_argument("--path", required=True, help="video file")
    p.add_argument("--out", default="out/debug_detect.mp4")
    p.add_argument("--fps", type=float)
    args = p.parse_args(argv)

    cfg = yaml.safe_load(Path(args.config).read_text())
    det = DogDetector(cfg)
    fps = args.fps or cfg["data"].get("fps", 8)
    Path(args.out).parent.mkdir(parents=True, exist_ok=True)
    writer, found, total = None, 0, 0
    with MediaSources(type="file", path=args.path, fps=fps, fast=True) as src:
        for _, frame in src.video():
            d = det.detect(frame)
            total += 1
            found += d is not None
            annotated = draw_debug(frame, d, det.zone, det.latencies_ms[-1] if det.latencies_ms else det.first_call_ms or 0.0)
            if writer is None:
                writer = DebugVideoWriter(args.out, fps)
            writer.write(annotated)
    if writer is not None:
        writer.release()
    s = det.latency_stats()
    print(f"{found}/{total} frames with a dog on {det.device}; latency mean {s['mean_ms']:.1f} ms, "
          f"p95 {s['p95_ms']:.1f} ms (first frame {det.first_call_ms or 0:.0f} ms excluded); wrote {args.out}")


if __name__ == "__main__":
    main()
