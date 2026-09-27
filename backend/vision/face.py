"""Face landmark estimation from the head crop.

    face = FaceLandmarker(cfg)               # reads `data.face`; loads the DogFLW TFLite landmark model
    lms = face.estimate(frame, kps)          # kps: canonical body keypoints from PoseEstimator.estimate()
    lms[MOUTH_CORNER_LEFT]                   # [x, y] in full-frame pixels; lms is None if no usable face

The model is the DogFLW 46-point landmark detector released by hugocornellier
(huggingface.co/hugocornellier/dog-face-landmarks, weights CC BY-NC 4.0: non-commercial use only).
`python scripts/fetch_face_model.py` downloads it into models/ once; it is offline afterwards. It runs
on CPU through TensorFlow Lite (no new dependency: it ships with tensorflow).

The head crop comes from the pose keypoints (nose, eyes, ears, jaw, mouth corners), not from a face
detector. The model has no confidence output, so "is the face visible?" is decided by gating: enough
head keypoints, a big enough crop, and a plausibility check on the result (landmarks mostly inside the
crop and not collapsed into a blob). Anything that fails returns None; we never guess.

Landmark indices follow the DogFLW scheme (spreadsheet linked from arxiv.org/abs/2405.11501). "Left"
and "right" are the annotators' labels, taken as given, same policy as keypoint_map.py.
"""

from __future__ import annotations

import argparse
import time
import warnings
from collections import deque
from pathlib import Path
from typing import Any, Callable

import cv2
import numpy as np

from backend.vision.keypoint_map import Keypoint
from backend.vision.videoio import DebugVideoWriter

# -- DogFLW landmark indices ------------------------------------------------------------------

N_LANDMARKS = 46

EAR_UPPER_BASE_LEFT, EAR_UPPER_BASE_RIGHT = 0, 1  # angle between medial ear base and forehead
EAR_TIP_LEFT, EAR_TIP_RIGHT = 6, 7
EAR_LOWER_BASE_LEFT, EAR_LOWER_BASE_RIGHT = 12, 13  # angle between lateral ear base and skull
BROW_LEFT, BROW_RIGHT = 14, 15
EYE_INNER_LEFT, EYE_INNER_RIGHT = 16, 17
EYE_OUTER_LEFT, EYE_OUTER_RIGHT = 18, 19
EYE_UPPER_LEFT, EYE_UPPER_RIGHT = 20, 21  # middle of the upper eyelid
EYE_LOWER_LEFT, EYE_LOWER_RIGHT = 22, 23
NOSE_UPPER = 25
NOSTRILS_MIDDLE = 32
NOSE_BOTTOM = 35
LIP_UPPER_MID = 38  # lower part of the upper lip, just below the nose
MOUTH_CORNER_LEFT, MOUTH_CORNER_RIGHT = 39, 40
LIP_LOWER_MID = 41  # top of the lower lip, or mid-tongue when panting
CHIN = 42
TONGUE_TIP = 45

# Landmark groups: ears 0-13 (2/3 are the upper bends, 4/5 the medial midpoints, 8/9 the lateral
# midpoints, 10/11 the lower 2/3 points), eyes 16-23, nose 24-35, mouth 36-45 (whisker pad to tongue).
EAR_LEFT = (0, 2, 4, 6, 8, 10, 12)
EAR_RIGHT = (1, 3, 5, 7, 9, 11, 13)
EYE_LEFT = (16, 18, 20, 22)
EYE_RIGHT = (17, 19, 21, 23)
NOSE = tuple(range(24, 36))
MOUTH = tuple(range(36, 46))
GROUPS: dict[str, tuple[int, ...]] = {
    "ear_left": EAR_LEFT,
    "ear_right": EAR_RIGHT,
    "brow": (BROW_LEFT, BROW_RIGHT),
    "eye_left": EYE_LEFT,
    "eye_right": EYE_RIGHT,
    "nose": NOSE,
    "mouth": MOUTH,
}

# Canonical body keypoints that describe the head (see keypoint_map.py); the head crop is built from these.
HEAD_KEYPOINTS: tuple[str, ...] = (
    "nose",
    "upper_jaw",
    "lower_jaw",
    "mouth_left",
    "mouth_right",
    "left_eye",
    "right_eye",
    "left_ear_base",
    "left_ear_tip",
    "right_ear_base",
    "right_ear_tip",
)
_SECONDARY_KEYPOINTS = ("upper_jaw", "lower_jaw", "mouth_left", "mouth_right")
_CORE_KEYPOINTS = tuple(n for n in HEAD_KEYPOINTS if n not in _SECONDARY_KEYPOINTS)
_EYES_OR_EAR_BASES = ("left_eye", "right_eye", "left_ear_base", "right_ear_base")
SECONDARY_MARGIN = 0.5  # jaw/mouth points count if within this fraction of the core box size outside it

INPUT_SIZE = 384
INSIDE_MARGIN = 0.05  # a landmark may fall this fraction of the crop outside it and still count as inside

Runner = Callable[[np.ndarray], np.ndarray]  # (1, 384, 384, 3) float32 RGB in 0..1 -> (92,) normalised x, y pairs


class FaceLandmarker:
    def __init__(self, cfg: dict, runner: Runner | None = None):
        """`runner` lets tests inject a fake model with the `Runner` signature above."""
        f = cfg["data"].get("face", {})
        self.crop_pad = f.get("crop_pad", 0.25)
        self.min_crop_px = f.get("min_crop_px", 48)
        self.min_head_points = f.get("min_head_points", 3)
        self.min_inside_frac = f.get("min_inside_frac", 0.9)
        self.min_spread = f.get("min_spread", 0.25)  # landmark extent must cover this fraction of the crop side
        self.runner = runner or self._load(f.get("model", "models/dog_face_landmarks_full.tflite"), f.get("threads", 4))
        self.latencies_ms: deque[float] = deque(maxlen=200)
        self.first_call_ms: float | None = None  # first call is slow (XNNPACK setup); kept out of stats
        self._warmed = runner is None
        if self._warmed:
            self.runner(np.zeros((1, INPUT_SIZE, INPUT_SIZE, 3), np.float32))

    @staticmethod
    def _load(model_path: str, threads: int) -> Runner:
        path = Path(model_path)
        if not path.exists():
            raise FileNotFoundError(f"{path} not found: run `python scripts/fetch_face_model.py` once (needs network)")
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")  # tf.lite.Interpreter deprecation notice; still works in TF 2.21
            import tensorflow as tf

            interp = tf.lite.Interpreter(model_path=str(path), num_threads=threads)
        interp.allocate_tensors()
        inp, out = interp.get_input_details()[0]["index"], interp.get_output_details()[0]["index"]

        def run(x: np.ndarray) -> np.ndarray:
            interp.set_tensor(inp, x)
            interp.invoke()
            return interp.get_tensor(out)[0].copy()

        return run

    def head_box(self, kps: dict[str, Keypoint | None]) -> tuple[float, float, float] | None:
        """Square head crop (x1, y1, side) in full-frame pixels from the head keypoints, or None.

        The box comes from the core points (nose, eyes, ears). Jaw and mouth-corner points are only used
        when they fall near that box: the pose model sometimes puts a confident jaw point on the grass
        far from the head, and one such point blew the box up to twice the size of the head.

        The gate: at least `min_head_points` head keypoints, one of them an eye or an ear base (a nose
        alone can sit anywhere on a blurry body), and a side of at least `min_crop_px`.
        """
        core = [(kps[n][0], kps[n][1]) for n in _CORE_KEYPOINTS if kps.get(n)]
        if not core or not any(kps.get(n) for n in _EYES_OR_EAR_BASES):
            return None
        c = np.array(core)
        (cx1, cy1), (cx2, cy2) = c.min(axis=0), c.max(axis=0)
        mx, my = (cx2 - cx1) * SECONDARY_MARGIN, (cy2 - cy1) * SECONDARY_MARGIN
        near = [
            (kps[n][0], kps[n][1])
            for n in _SECONDARY_KEYPOINTS
            if kps.get(n) and cx1 - mx <= kps[n][0] <= cx2 + mx and cy1 - my <= kps[n][1] <= cy2 + my
        ]
        pts = core + near
        if len(pts) < self.min_head_points:
            return None
        a = np.array(pts)
        (x1, y1), (x2, y2) = a.min(axis=0), a.max(axis=0)
        side = max(x2 - x1, y2 - y1) * (1 + 2 * self.crop_pad)
        if side < self.min_crop_px:
            return None
        cx, cy = (x1 + x2) / 2, (y1 + y2) / 2
        return float(cx - side / 2), float(cy - side / 2), float(side)

    def estimate(self, frame: np.ndarray, kps: dict[str, Keypoint | None]) -> list[list[float]] | None:
        """46 [x, y] landmarks in full-frame pixels (DogFLW order), or None if no usable face."""
        box = self.head_box(kps)
        if box is None:
            return None
        x1, y1, side = box
        s = INPUT_SIZE / side
        # Warp so parts of the square outside the frame are black instead of shrinking the crop.
        crop = cv2.warpAffine(frame, np.array([[s, 0, -x1 * s], [0, s, -y1 * s]], np.float32), (INPUT_SIZE, INPUT_SIZE))
        x = cv2.cvtColor(crop, cv2.COLOR_BGR2RGB).astype(np.float32)[None] / 255.0

        t = time.perf_counter()
        out = np.asarray(self.runner(x), dtype=np.float64).reshape(-1)
        ms = (time.perf_counter() - t) * 1000
        if self.first_call_ms is None and self._warmed:
            self.first_call_ms = ms
        else:
            self.latencies_ms.append(ms)

        if out.size != 2 * N_LANDMARKS or not np.isfinite(out).all():
            return None
        norm = out.reshape(N_LANDMARKS, 2)
        if not self._plausible(norm):
            return None
        return [[float(x1 + nx * side), float(y1 + ny * side)] for nx, ny in norm]

    def _plausible(self, norm: np.ndarray) -> bool:
        """Landmarks (normalised to the crop) mostly inside it and not collapsed into a blob."""
        inside = ((norm >= -INSIDE_MARGIN) & (norm <= 1 + INSIDE_MARGIN)).all(axis=1).mean()
        spread = float((norm.max(axis=0) - norm.min(axis=0)).max())
        return inside >= self.min_inside_frac and spread >= self.min_spread

    def latency_stats(self) -> dict[str, float]:
        """Mean, median and p95 landmark-model latency in ms over the last 200 calls."""
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

_GROUP_COLORS = {  # BGR
    "ear_left": (255, 120, 0),
    "ear_right": (255, 200, 0),
    "brow": (200, 200, 200),
    "eye_left": (0, 220, 0),
    "eye_right": (120, 255, 120),
    "nose": (0, 140, 255),
    "mouth": (0, 230, 255),
}


def draw_face(frame: np.ndarray, lms: list[list[float]] | None, box: tuple[float, float, float] | None) -> np.ndarray:
    out = frame.copy()
    if box is not None:
        x1, y1, side = box
        cv2.rectangle(out, (int(x1), int(y1)), (int(x1 + side), int(y1 + side)), (255, 0, 255) if lms else (0, 0, 220), 1)
    if lms:
        for group, idxs in GROUPS.items():
            for i in idxs:
                cv2.circle(out, (int(lms[i][0]), int(lms[i][1])), 2, _GROUP_COLORS[group], -1)
        for i in (LIP_UPPER_MID, LIP_LOWER_MID, TONGUE_TIP, EAR_TIP_LEFT, EAR_TIP_RIGHT):
            cv2.circle(out, (int(lms[i][0]), int(lms[i][1])), 4, (255, 255, 255), 1)
            cv2.putText(out, str(i), (int(lms[i][0]) + 4, int(lms[i][1]) - 4), cv2.FONT_HERSHEY_SIMPLEX, 0.4, (255, 255, 255), 1)
    return out


def main(argv: list[str] | None = None) -> None:
    import yaml

    from backend.sources import MediaSources
    from backend.vision.detect import DogDetector, draw_debug
    from backend.vision.pose import PoseEstimator, draw_skeleton

    p = argparse.ArgumentParser(description="Run detection + body pose + face landmarks on a clip and write an annotated video.")
    p.add_argument("--config", default="config.yaml")
    p.add_argument("--path", required=True, help="video file")
    p.add_argument("--out", default="out/debug_face.mp4")
    p.add_argument("--fps", type=float)
    args = p.parse_args(argv)

    cfg = yaml.safe_load(Path(args.config).read_text())
    det, pose, face = DogDetector(cfg), PoseEstimator(cfg), FaceLandmarker(cfg)
    fps = args.fps or cfg["data"].get("fps", 8)
    Path(args.out).parent.mkdir(parents=True, exist_ok=True)
    writer, found, faces, total, per_frame_ms = None, 0, 0, 0, []
    with MediaSources(type="file", path=args.path, fps=fps, fast=True) as src:
        for _, frame in src.video():
            t = time.perf_counter()
            d = det.detect(frame)
            kps = pose.estimate(frame, d.bbox) if d is not None else {}
            lms = face.estimate(frame, kps) if kps else None
            per_frame_ms.append((time.perf_counter() - t) * 1000)
            total += 1
            found += d is not None
            faces += lms is not None
            annotated = draw_face(draw_skeleton(draw_debug(frame, d, det.zone, per_frame_ms[-1]), kps), lms, face.head_box(kps) if kps else None)
            if writer is None:
                writer = DebugVideoWriter(args.out, fps)
            writer.write(annotated)
    if writer is not None:
        writer.release()
    steady = np.array(per_frame_ms[1:] or per_frame_ms or [0.0])
    s = face.latency_stats()
    print(f"{found}/{total} frames with a dog; face landmarks on {faces}/{found} of those "
          f"({faces / max(found, 1):.0%}); detect+pose+face mean {steady.mean():.0f} ms = {1000 / max(steady.mean(), 1e-9):.1f} fps "
          f"(median {np.median(steady):.0f} ms, p95 {np.percentile(steady, 95):.0f} ms); "
          f"face alone mean {s['mean_ms']:.0f} / median {s['median_ms']:.0f} ms over {s['n']} calls; wrote {args.out}")


if __name__ == "__main__":
    main()
