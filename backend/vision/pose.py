"""Body keypoint estimation adapter (pretrained quadruped pose).

    det = DogDetector(cfg)
    pose = PoseEstimator(cfg)                # reads `data.pose`; loads DeepLabCut SuperAnimal-Quadruped
    kps = pose.estimate(frame, det.detect(frame).bbox)
    kps["tail_tip"]                          # (x, y, conf) in full-frame pixels, or None

The model is DeepLabCut's SuperAnimal-Quadruped (HRNet-W32, top-down), run on MPS when available.
Its weights are downloaded once into the deeplabcut package folder and are offline afterwards, so the
first run on a new machine needs a network (do it before the offline demo).

We crop the dog ourselves (padded bbox, same helper as detect.py) and hand the model the crop with a
box covering all of it. Passing the full frame plus a bbox and letting DeepLabCut crop looked wrong:
keypoints landed hundreds of pixels off the dog and inference was 2-3x slower.

Any keypoint the model places outside the crop is discarded: the model sometimes returns
high-confidence points far from the dog (seen most on jaw points), and confidence alone does not catch
them.
"""

from __future__ import annotations

import argparse
import time
from collections import deque
from pathlib import Path
from typing import Any

import cv2
import numpy as np

from backend.vision.detect import BBox, crop_padded, pick_device
from backend.vision.keypoint_map import CANONICAL_NAMES, SKELETON, Keypoint, to_canonical
from backend.vision.videoio import DebugVideoWriter

OUTSIDE_MARGIN = 0.05  # keypoints may fall this fraction of the crop size outside it before being dropped


class PoseEstimator:
    def __init__(self, cfg: dict, runner: Any = None, bodyparts: list[str] | None = None, device: str | None = None):
        """`runner`/`bodyparts` let tests inject a fake with DeepLabCut's `inference()` interface."""
        data = cfg["data"]
        p = data.get("pose", {})
        self.conf_thr = data.get("keypoint_conf_threshold", 0.3)
        self.pad = p.get("crop_pad", 0.15)
        self.device = device or (pick_device() if p.get("device", "auto") == "auto" else p["device"])

        owns_model = runner is None
        if owns_model:
            runner, bodyparts = self._load(p.get("superanimal", "superanimal_quadruped"), p.get("model", "hrnet_w32"))
        if bodyparts is None:
            raise ValueError("bodyparts (the model's keypoint names, in output order) is required with a custom runner")
        self.runner = runner
        self.bodyparts = list(bodyparts)

        self.latencies_ms: deque[float] = deque(maxlen=200)
        self.first_call_ms: float | None = None  # first call compiles on MPS; kept out of stats
        self._warmed = owns_model
        if owns_model:
            self._infer(np.zeros((256, 256, 3), np.uint8))

    def _load(self, superanimal: str, model_name: str) -> tuple[Any, list[str]]:
        from deeplabcut.pose_estimation_pytorch.apis.utils import get_pose_inference_runner
        from deeplabcut.pose_estimation_pytorch.config.pose import PoseConfig
        from deeplabcut.pose_estimation_pytorch.modelzoo.utils import get_super_animal_snapshot_path

        snapshot = get_super_animal_snapshot_path(superanimal, model_name)  # downloads once if missing
        model_cfg = PoseConfig.build_for_superanimal_inference(
            super_animal=superanimal, model_name=model_name, detector_name=None, max_individuals=1, device=self.device
        )
        runner = get_pose_inference_runner(
            model_cfg, snapshot_path=snapshot, batch_size=1, device=self.device, max_individuals=1
        )
        return runner, list(model_cfg["metadata"]["bodyparts"])

    def _infer(self, crop: np.ndarray) -> np.ndarray:
        """Raw (K, 3) array of x, y, conf in crop pixel coords."""
        h, w = crop.shape[:2]
        out = self.runner.inference([(crop, {"bboxes": np.array([[0, 0, w, h]], dtype=float)})])
        poses = np.asarray(out[0]["bodyparts"])
        return poses[0] if poses.ndim == 3 and len(poses) else np.zeros((len(self.bodyparts), 3))

    def estimate(self, frame: np.ndarray, bbox: BBox) -> dict[str, Keypoint | None]:
        """Canonical keypoints for the dog in `bbox`, in full-frame pixel coords.

        Every canonical name is a key; the value is (x, y, conf), or None when the point is missing,
        below the confidence threshold or outside the dog's crop.
        """
        crop, (ox, oy) = crop_padded(frame, bbox, self.pad)
        h, w = crop.shape[:2]
        if h < 2 or w < 2:
            return {name: None for name in CANONICAL_NAMES}

        t = time.perf_counter()
        pts = self._infer(crop)
        ms = (time.perf_counter() - t) * 1000
        if self.first_call_ms is None and self._warmed:
            self.first_call_ms = ms
        else:
            self.latencies_ms.append(ms)

        mx, my = OUTSIDE_MARGIN * w, OUTSIDE_MARGIN * h
        raw: dict[str, tuple[float, float, float]] = {}
        for name, (x, y, c) in zip(self.bodyparts, pts):
            inside = -mx <= x <= w + mx and -my <= y <= h + my
            raw[name] = (float(x) + ox, float(y) + oy, float(c) if inside else 0.0)
        return to_canonical(raw, self.conf_thr)

    def latency_stats(self) -> dict[str, float]:
        """Mean, median and p95 estimate() model latency in ms over the last 200 calls."""
        if not self.latencies_ms:
            return {"mean_ms": 0.0, "median_ms": 0.0, "p95_ms": 0.0, "n": 0}
        a = np.array(self.latencies_ms)
        return {
            "mean_ms": float(a.mean()),
            "median_ms": float(np.median(a)),
            "p95_ms": float(np.percentile(a, 95)),
            "n": len(a),
        }


# -- CLI: annotated debug video ---------------------------------------------------------------


def draw_skeleton(frame: np.ndarray, kps: dict[str, Keypoint | None]) -> np.ndarray:
    out = frame.copy()
    for a, b in SKELETON:
        if kps.get(a) and kps.get(b):
            cv2.line(out, (int(kps[a][0]), int(kps[a][1])), (int(kps[b][0]), int(kps[b][1])), (0, 200, 255), 2)
    for name, p in kps.items():
        if p:
            cv2.circle(out, (int(p[0]), int(p[1])), 4, (0, 255, 0), -1)
            if name in ("tail_tip", "left_ear_tip", "right_ear_tip"):
                cv2.putText(out, name, (int(p[0]) + 5, int(p[1])), cv2.FONT_HERSHEY_SIMPLEX, 0.45, (0, 255, 255), 1)
    return out


def main(argv: list[str] | None = None) -> None:
    import yaml

    from backend.sources import MediaSources
    from backend.vision.detect import DogDetector, draw_debug

    p = argparse.ArgumentParser(description="Run dog detection + body pose on a clip and write an annotated video.")
    p.add_argument("--config", default="config.yaml")
    p.add_argument("--path", required=True, help="video file")
    p.add_argument("--out", default="out/debug_pose.mp4")
    p.add_argument("--fps", type=float)
    args = p.parse_args(argv)

    cfg = yaml.safe_load(Path(args.config).read_text())
    det, pose = DogDetector(cfg), PoseEstimator(cfg)
    fps = args.fps or cfg["data"].get("fps", 8)
    Path(args.out).parent.mkdir(parents=True, exist_ok=True)
    writer, found, total, per_frame_ms, seen = None, 0, 0, [], {n: 0 for n in CANONICAL_NAMES}
    with MediaSources(type="file", path=args.path, fps=fps, fast=True) as src:
        for _, frame in src.video():
            t = time.perf_counter()
            d = det.detect(frame)
            kps = pose.estimate(frame, d.bbox) if d is not None else {}
            per_frame_ms.append((time.perf_counter() - t) * 1000)
            total += 1
            found += d is not None
            for name, pt in kps.items():
                seen[name] += pt is not None
            annotated = draw_skeleton(draw_debug(frame, d, det.zone, per_frame_ms[-1]), kps)
            if writer is None:
                writer = DebugVideoWriter(args.out, fps)
            writer.write(annotated)
    if writer is not None:
        writer.release()
    steady = np.array(per_frame_ms[1:] or per_frame_ms or [0.0])
    s = pose.latency_stats()
    print(f"{found}/{total} frames with a dog on {pose.device}; detect+pose mean {steady.mean():.0f} ms "
          f"= {1000 / max(steady.mean(), 1e-9):.1f} fps (median {np.median(steady):.0f} ms, p95 {np.percentile(steady, 95):.0f} ms); "
          f"pose alone mean {s['mean_ms']:.0f} / median {s['median_ms']:.0f} ms; wrote {args.out}")
    if found:
        print("keypoint present in % of dog frames: " + ", ".join(f"{n} {seen[n] / found:.0%}" for n in CANONICAL_NAMES))


if __name__ == "__main__":
    main()
