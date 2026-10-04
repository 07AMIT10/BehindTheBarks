"""Mobile inference runners for on-device detection, pose, face, and audio models.

Implements Python equivalents of the target Android perception pipeline:
- MobileDetector: YOLOv26n INT8/FP32 (and ONNX) with letterbox and NMS-free decode
- MobilePose: RTMPose AP-10K with SimCC head decoding to canonical keypoints
- MobileAudio: YAMNet TFLite on 15,600-sample windows
All pre/post-processing parameters are sourced from backend/vision/mobile_spec.json.
"""

from __future__ import annotations

import csv
import json
import logging
import time
from pathlib import Path
from typing import Any, Mapping, Sequence

import cv2
import numpy as np

from backend.vision.keypoint_map import CANONICAL_NAMES, Keypoint
from backend.vision.detect import BBox, crop_padded

log = logging.getLogger(__name__)

ROOT = Path(__file__).resolve().parent.parent.parent
SPEC_PATH = ROOT / "backend" / "vision" / "mobile_spec.json"


def load_mobile_spec(path: Path | str | None = None) -> dict[str, Any]:
    """Load the mobile perception specification JSON."""
    p = Path(path) if path is not None else SPEC_PATH
    if not p.is_file():
        raise FileNotFoundError(f"mobile_spec.json not found at {p}")
    return json.loads(p.read_text())


# -- Geometry and Preprocessing --------------------------------------------------------


def letterbox(im: np.ndarray, size: int = 320, pad: int = 114) -> tuple[np.ndarray, float, tuple[int, int]]:
    """Resize preserving aspect, pad to `size`; returns (image, scale, (left, top))."""
    shape = im.shape[:2]
    r = min(size / shape[0], size / shape[1])
    new_unpad = (round(shape[1] * r), round(shape[0] * r))
    dw, dh = (size - new_unpad[0]) / 2, (size - new_unpad[1]) / 2
    top, bottom, left, right = round(dh - 0.1), round(dh + 0.1), round(dw - 0.1), round(dw + 0.1)
    out = cv2.resize(im, new_unpad, interpolation=cv2.INTER_LINEAR) if shape[::-1] != new_unpad else im
    border = cv2.copyMakeBorder(out, top, bottom, left, right, cv2.BORDER_CONSTANT, value=(pad,) * 3)
    return border, r, (left, top)


def unletterbox(box: Sequence[float], scale: float, offset: tuple[int, int]) -> BBox:
    """Invert letterbox coordinate transform back to full frame pixels."""
    left, top = offset
    return (box[0] / scale, (box[1] - top) / scale, box[2] / scale, (box[3] - top) / scale)


def detector_input(im: np.ndarray, size: int = 320) -> np.ndarray:
    """Letterboxed BGR image -> [1, 3, size, size] float32, RGB / 255.0."""
    rgb = cv2.cvtColor(im, cv2.COLOR_BGR2RGB).astype(np.float32) / 255.0
    return np.ascontiguousarray(rgb.transpose(2, 0, 1)[None], dtype=np.float32)


def pose_input(
    crop: np.ndarray,
    size: int = 256,
    mean: Sequence[float] = (0.485, 0.456, 0.406),
    std: Sequence[float] = (0.229, 0.224, 0.225),
) -> np.ndarray:
    """BGR crop -> [1, 3, size, size] float32, ImageNet normalized."""
    resized = cv2.resize(crop, (size, size), interpolation=cv2.INTER_LINEAR)
    rgb = cv2.cvtColor(resized, cv2.COLOR_BGR2RGB).astype(np.float32) / 255.0
    mean_arr = np.array(mean, dtype=np.float32)
    std_arr = np.array(std, dtype=np.float32)
    x = (rgb - mean_arr) / std_arr
    return np.ascontiguousarray(x.transpose(2, 0, 1)[None], dtype=np.float32)


def iou(a: Sequence[float], b: Sequence[float]) -> float:
    ix = max(0.0, min(a[2], b[2]) - max(a[0], b[0]))
    iy = max(0.0, min(a[3], b[3]) - max(a[1], b[1]))
    inter = ix * iy
    union = (a[2] - a[0]) * (a[3] - a[1]) + (b[2] - b[0]) * (b[3] - b[1]) - inter
    return inter / union if union > 0 else 0.0


def nms(boxes: np.ndarray, scores: np.ndarray, thr: float) -> list[int]:
    """Greedy NMS over boxes, returning indices in descending-score order."""
    if not len(boxes):
        return []
    order = np.argsort(-scores, kind="stable")
    keep: list[int] = []
    while order.size:
        i = int(order[0])
        keep.append(i)
        if order.size == 1:
            break
        rest = order[1:]
        overlaps = np.array([iou(boxes[i], boxes[j]) for j in rest])
        order = rest[overlaps <= thr]
    return keep


# -- Model Runtimes --------------------------------------------------------------------


class LiteRtRuntime:
    """LiteRT / TFLite interpreter on 1 thread for deterministic mobile execution."""

    def __init__(self, path: Path | str, num_threads: int = 1):
        self.path = Path(path)
        if not self.path.exists():
            raise FileNotFoundError(f"LiteRT model not found: {self.path}")

        try:
            from ai_edge_litert import interpreter as itp
            self.interp = itp.Interpreter(model_path=str(self.path), num_threads=num_threads)
        except ImportError:
            import tensorflow as tf
            self.interp = tf.lite.Interpreter(model_path=str(self.path), num_threads=num_threads)

        self.interp.allocate_tensors()
        self.inp_details = self.interp.get_input_details()
        self.out_details = self.interp.get_output_details()

    def __call__(self, x: np.ndarray) -> list[np.ndarray]:
        inp = self.inp_details[0]
        self.interp.set_tensor(inp["index"], np.ascontiguousarray(x.astype(inp["dtype"])))
        self.interp.invoke()
        return [np.array(self.interp.get_tensor(o["index"])) for o in self.out_details]


class OnnxRuntime:
    """ONNX Runtime session on 1 thread for deterministic mobile execution."""

    def __init__(self, path: Path | str, num_threads: int = 1):
        import onnxruntime as ort

        self.path = Path(path)
        if not self.path.exists():
            raise FileNotFoundError(f"ONNX model not found: {self.path}")

        opts = ort.SessionOptions()
        opts.intra_op_num_threads = num_threads
        opts.inter_op_num_threads = num_threads
        opts.log_severity_level = 3
        self.sess = ort.InferenceSession(str(self.path), opts, providers=["CPUExecutionProvider"])
        self.inp_name = self.sess.get_inputs()[0].name

    def __call__(self, x: np.ndarray) -> list[np.ndarray]:
        res = self.sess.run(None, {self.inp_name: np.ascontiguousarray(x.astype(np.float32))})
        return list(res)


def load_runtime(path: Path | str, num_threads: int = 1) -> Any:
    p = Path(path)
    if p.suffix == ".onnx":
        return OnnxRuntime(p, num_threads=num_threads)
    return LiteRtRuntime(p, num_threads=num_threads)


# -- Mobile Perception Classes ---------------------------------------------------------


class MobileDetector:
    """Mobile dog detector runner: handles letterbox, LiteRT/ONNX inference, and output decode."""

    def __init__(
        self,
        spec: dict[str, Any] | None = None,
        model_path: Path | str | None = None,
        runtime: Any = None,
        kind: str | None = None,
    ):
        self.spec = spec or load_mobile_spec()
        det_spec = self.spec.get("detector", {})
        self.input_size = int(det_spec.get("input_size", 320))
        self.pad_color = int(det_spec.get("letterbox_pad_color", 114))
        self.dog_class = int(det_spec.get("dog_class_coco", 16))
        self.conf_thr = float(det_spec.get("conf_threshold", 0.25))

        if runtime is not None:
            self.runtime = runtime
        else:
            path = model_path or det_spec.get("default_model", "out/android_models/det_yolo26n_320_int8.tflite")
            p = ROOT / path if not Path(path).is_absolute() else Path(path)
            self.runtime = load_runtime(p)

        self.kind = kind or self._detect_kind()

    def _detect_kind(self) -> str:
        """Infer model output architecture based on output tensor shapes."""
        if hasattr(self.runtime, "out_details"):
            outs = self.runtime.out_details
            if outs and len(outs) == 1:
                shape = list(outs[0]["shape"])
                if len(shape) == 3 and shape[1] == 84 and shape[2] == 2100:
                    return "raw_head"
                if len(shape) == 3 and shape[1] == 300 and shape[2] == 6:
                    return "end2end"
        return "raw_head"

    def predict_raw(self, frame: np.ndarray) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
        """Detect dogs in `frame`. Returns (boxes [N, 4], scores [N], classes [N])."""
        lb, scale, offset = letterbox(frame, size=self.input_size, pad=self.pad_color)
        inp = detector_input(lb, size=self.input_size)
        outs = self.runtime(inp)

        if self.kind == "end2end":
            out = np.asarray(outs[0]).reshape(-1, 6)
            keep = np.flatnonzero((out[:, 5] == self.dog_class) & (out[:, 4] >= self.conf_thr))
            if keep.size == 0:
                return np.zeros((0, 4), dtype=np.float32), np.zeros((0,), dtype=np.float32), np.zeros((0,), dtype=np.int32)
            boxes = np.array([unletterbox(out[i, :4], scale, offset) for i in keep], dtype=np.float32)
            scores = np.asarray(out[keep, 4], dtype=np.float32)
            classes = np.full(len(keep), self.dog_class, dtype=np.int32)
            return boxes, scores, classes

        # raw_head: [1, 84, 2100] = (cx, cy, w, h, 80 classes)
        out = np.asarray(outs[0]).reshape(84, -1)
        scores = out[4 + self.dog_class]
        keep = np.flatnonzero(scores >= self.conf_thr)
        if keep.size == 0:
            return np.zeros((0, 4), dtype=np.float32), np.zeros((0,), dtype=np.float32), np.zeros((0,), dtype=np.int32)

        cx, cy, w, h = out[0, keep], out[1, keep], out[2, keep], out[3, keep]
        px = np.stack([cx - w / 2, cy - h / 2, cx + w / 2, cy + h / 2], axis=1) * float(self.input_size)
        boxes = np.array([unletterbox(b, scale, offset) for b in px], dtype=np.float32)
        scores_arr = np.asarray(scores[keep], dtype=np.float32)
        classes_arr = np.full(len(keep), self.dog_class, dtype=np.int32)
        return boxes, scores_arr, classes_arr


class MobilePose:
    """Mobile animal pose estimator: RTMPose AP-10K with SimCC decoding to canonical keypoints."""

    def __init__(
        self,
        spec: dict[str, Any] | None = None,
        model_path: Path | str | None = None,
        runtime: Any = None,
    ):
        self.spec = spec or load_mobile_spec()
        pose_spec = self.spec.get("pose", {})
        self.input_size = int(pose_spec.get("input_size", 256))
        self.crop_pad = float(pose_spec.get("crop_pad", 0.15))
        self.conf_thr = float(pose_spec.get("conf_threshold", 0.3))
        self.outside_margin_frac = float(pose_spec.get("outside_margin_frac", 0.05))
        self.split_ratio = float(pose_spec.get("simcc_split_ratio", 2.0))
        self.mean = tuple(pose_spec.get("imagenet_mean", (0.485, 0.456, 0.406)))
        self.std = tuple(pose_spec.get("imagenet_std", (0.229, 0.224, 0.225)))
        self.keypoint_names = tuple(pose_spec.get("keypoint_names", ()))
        self.mapping = dict(pose_spec.get("keypoint_mapping", {}))

        if runtime is not None:
            self.runtime = runtime
        else:
            path = model_path or pose_spec.get("default_model", "out/android_models/pose_rtmpose_ap10k_litert.tflite")
            p = ROOT / path if not Path(path).is_absolute() else Path(path)
            self.runtime = load_runtime(p)

    def estimate(self, frame: np.ndarray, bbox: BBox) -> dict[str, Keypoint | None]:
        """Estimate canonical keypoints for the dog in bbox in full-frame pixels."""
        crop, (ox, oy) = crop_padded(frame, bbox, self.crop_pad)
        h, w = crop.shape[:2]
        if h < 2 or w < 2:
            return {n: None for n in CANONICAL_NAMES}

        inp = pose_input(crop, size=self.input_size, mean=self.mean, std=self.std)
        outs = self.runtime(inp)
        simcc_x = np.asarray(outs[0], np.float32)
        simcc_y = np.asarray(outs[1], np.float32)
        if simcc_x.ndim == 3:
            simcc_x = simcc_x[0]
        if simcc_y.ndim == 3:
            simcc_y = simcc_y[0]

        # SimCC split ratio decode: argmax / split_ratio
        ix = simcc_x.argmax(axis=-1)
        iy = simcc_y.argmax(axis=-1)
        size_x = simcc_x.shape[-1] / self.split_ratio
        size_y = simcc_y.shape[-1] / self.split_ratio
        kx = self.input_size / size_x
        ky = self.input_size / size_y
        xs_in = ix / self.split_ratio * kx
        ys_in = iy / self.split_ratio * ky
        score = simcc_x.max(axis=-1) * simcc_y.max(axis=-1)

        # Map from 256x256 stretch to crop pixel coords
        sx = w / float(self.input_size)
        sy = h / float(self.input_size)

        mx, my = self.outside_margin_frac * w, self.outside_margin_frac * h
        out: dict[str, Keypoint | None] = {n: None for n in CANONICAL_NAMES}

        for i, raw_name in enumerate(self.keypoint_names):
            canon = self.mapping.get(raw_name)
            if canon is None:
                continue
            cx = float(xs_in[i]) * sx
            cy = float(ys_in[i]) * sy
            c = float(np.clip(score[i], 0.0, 1.0))
            if c < self.conf_thr or not (-mx <= cx <= w + mx) or not (-my <= cy <= h + my):
                continue
            out[canon] = (cx + ox, cy + oy, c)

        return out


class MobileAudio:
    """Mobile YAMNet audio classifier runner on 15,600-sample windows."""

    def __init__(
        self,
        spec: dict[str, Any] | None = None,
        model_path: Path | str | None = None,
        runtime: Any = None,
        class_names: Sequence[str] | None = None,
    ):
        self.spec = spec or load_mobile_spec()
        audio_spec = self.spec.get("audio", {})
        self.window_samples = int(audio_spec.get("window_samples", 15600))
        self.pcm_scale = float(audio_spec.get("pcm_scale", 32768.0))

        if runtime is not None:
            self.runtime = runtime
        else:
            path = model_path or audio_spec.get("default_model", "out/android_models/audio_yamnet.tflite")
            p = ROOT / path if not Path(path).is_absolute() else Path(path)
            self.runtime = load_runtime(p)

        self.class_names = list(class_names) if class_names is not None else self._load_class_names()

    @staticmethod
    def _load_class_names() -> list[str]:
        map_path = ROOT / "models" / "yamnet" / "assets" / "yamnet_class_map.csv"
        if map_path.is_file():
            with open(map_path, newline="") as f:
                return [row["display_name"] for row in csv.DictReader(f)]
        return [f"class_{i}" for i in range(521)]

    def __call__(self, window: np.ndarray) -> np.ndarray:
        """Score a 15,600-sample window. Accepts float32 [-1, 1] or int16 PCM."""
        x = np.asarray(window)
        if np.issubdtype(x.dtype, np.integer):
            x = x.astype(np.float32) / self.pcm_scale
        else:
            x = x.astype(np.float32)

        x = x.reshape(-1)
        if len(x) < self.window_samples:
            padded = np.zeros(self.window_samples, dtype=np.float32)
            padded[:len(x)] = x
            x = padded
        elif len(x) > self.window_samples:
            x = x[:self.window_samples]

        outs = self.runtime(x)
        scores = np.asarray(outs[0], dtype=np.float32)
        if scores.ndim == 2:
            scores = scores.max(axis=0)  # max-pool over frames -> (521,)
        return scores.reshape(-1)
