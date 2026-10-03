#!/usr/bin/env python
"""Task 0.2: how accurate is each exported Android candidate, against the server stack it would replace?

    python scripts/bakeoff_models.py --clips all --out out/bakeoff.csv
    python scripts/bakeoff_models.py --clips waiting_corgi --models det_yolo26n_320_fp32 --limit 20
    python scripts/bakeoff_models.py --fit-effdet-anchors          # (re)derive the EfficientDet anchor table

**What this measures.** One CSV row per (candidate, clip) with `det_recall_vs_ref`, `mean_iou_vs_ref`, a per-canonical
keypoint `kp_pck@0.1_vs_ref.<name>`, `tail_tip_visible_rate`, `face_ok_rate` and `ms_mean_cpu`. The reference is the
*current server stack*, re-run here on the same frames the candidates see: YOLO11s@640 for the box
(`backend/vision/detect.py`), DeepLabCut SuperAnimal-Quadruped HRNet-W32 for the keypoints (`backend/vision/pose.py`
and the canonical names in `backend/vision/keypoint_map.py`), the DogFLW model for the face, YAMNet for audio. No rule
is re-tuned and no precomputed events file is read: the reference is what the code does today.

**Four choices, because "vs ref" is otherwise not comparable.**

  * Pose is fed the **reference's** box, so a pose row measures pose and not detect+pose compounded. Detection
    accuracy has its own rows.
  * The face landmarker is fed the **reference's** keypoints, because the face file under test is a byte-identical
    copy of the server's and the only open question is whether it runs on the phone at the same rate; mixing in a
    candidate's head box would make the number depend on a model the row is not about.
  * Detection is compared against the reference's **unsmoothed** box (`Detection.raw_bbox`): the EMA in
    `detect.py` is a temporal filter the candidates do not have and it would flatter a noisy model.
  * `det_recall_vs_ref` is over frames the reference found a dog in; `mean_iou_vs_ref` is the mean IoU over *those
    same frames*, with a missed frame scoring 0, so one number says how well the candidate reproduces the server's
    box and the other says how often it finds one at all.

**Each candidate runs the exact exported file** through `ai_edge_litert` (or `onnxruntime` for a `.onnx`) with one
thread. That is a rough proxy for phone cost only; Task 0.3 measures real latency, memory and thermal.

**A gap in a keypoint schema is a result, not a silent zero.** AP-10K has 17 keypoints with no ear points and no
tail tip, so a pose candidate cannot be scored on the tail at all. Every row carries `n_mapped` and
`unmapped_canonical_kps`, PCK columns exist only for *mapped* canonical names, and the aggregate averages only
those. A candidate that failed to export keeps its row with its failure reason, so the CSV is the complete record
of Task 0.1's candidate set.

Weights: every candidate file is third-party and git-ignored; `license` is a column so Phase 0.3's distribution
decision (D2) has it next to the accuracy numbers.

**What `ms_mean_cpu` is, exactly.** For a candidate it is graph time: `set_tensor + invoke + read outputs` on one
thread, with the first (delegate-setup) call dropped, crop/letterbox/decode excluded. The reference row's number is
the whole `detect + pose + face` call of the server stack -- preprocessing inside it -- over that clip's frames
only, and it times all three models at once. So the column ranks candidates against their own modality and is a
rough proxy at best; Task 0.3 measures real per-stage latency, memory and thermal on the phone.
"""

from __future__ import annotations

import argparse
import csv
import json
import logging
import math
import sys
import time
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Protocol

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import cv2  # noqa: E402
import numpy as np  # noqa: E402

from backend.vision.detect import BBox, crop_padded  # noqa: E402
from backend.vision.keypoint_map import CANONICAL_NAMES, Keypoint, SUPERANIMAL_TO_CANONICAL  # noqa: E402

log = logging.getLogger(__name__)

DEFAULT_EXPORT_DIR = ROOT / "out" / "android_models"
DEFAULT_OUT = ROOT / "out" / "bakeoff.csv"
FALLBACK = ROOT / "data" / "fallback"
MANIFEST = FALLBACK / "manifest.json"

CONF_THR = 0.25  # config.yaml data.detect.conf, applied to every candidate so the comparison is like-for-like
CROP_PAD = 0.15  # config.yaml data.pose.crop_pad: the phone must crop the way the server does
PCK_THRESH = 0.1  # PCK radius as a fraction of the reference box's diagonal (DeepLabCut's usual 0.1)
POSE_SIZE = 256  # every pose candidate here is exported at 256x256
KPT_CONF_THR = 0.3  # config.yaml data.keypoint_conf_threshold
OUTSIDE_MARGIN_FRAC = 0.05  # a keypoint further than this outside the crop is dropped, as in pose.py
THREADS = 1  # one thread everywhere: a phone is not an 8-core server
DOG_COCO = 16  # COCO index of "dog" in the 80-class list (yolo26n, picodet)
DOG_MEDIAPIPE = 17  # ... and in MediaPipe's 90-class list (effdet_lite0); the two lists are not the same
YAMNET_WINDOW = 15600  # 0.975 s at 16 kHz: one YAMNet score frame (backend/audio/yamnet_events.py)

# AP-10K's 17-keypoint schema in mmpose's output order (configs/_base_/datasets/ap10k.py, RTMPose AP-10K). It has a
# root-of-tail point and no ears, no jaw and no tail tip, which `unmapped_canonical_kps` says per row.
AP10K_NAMES: tuple[str, ...] = (
    "L_Eye", "R_Eye", "Nose", "Neck", "Root of tail",
    "L_Shoulder", "L_Elbow", "L_F_Paw", "R_Shoulder", "R_Elbow", "R_F_Paw",
    "L_Hip", "L_Knee", "L_B_Paw", "R_Hip", "R_Knee", "R_B_Paw",
)
# "Root of tail" is AP-10K's rump point: its skeleton runs Neck -> Root of tail -> L_Hip, which is where SuperAnimal's
# back_end (our `hip`) sits. Mapping it by that role keeps `hip` measurable and leaves `tail_base` unmapped, which is
# the honest reading -- AP-10K has no point on the tail itself, so no RTMPose candidate can do tail-wag.
AP10K_TO_CANONICAL: dict[str, str] = {
    "L_Eye": "left_eye", "R_Eye": "right_eye", "Nose": "nose", "Neck": "withers", "Root of tail": "hip",
    "L_F_Paw": "left_front_paw", "R_F_Paw": "right_front_paw",
    "L_B_Paw": "left_back_paw", "R_B_Paw": "right_back_paw",
}

# The keypoints the plan cares about: their PCK columns are the first ones a reader looks for.
CRITICAL_KPS: tuple[str, ...] = (
    "tail_base", "tail_tip", "withers", "hip",
    "left_ear_base", "left_ear_tip", "right_ear_base", "right_ear_tip",
)

MODALITY: dict[str, str] = {
    "det_yolo26n_320_fp32": "det", "det_yolo26n_320_int8": "det", "det_effdet_lite0_320_int8": "det",
    "det_picodet_s_320": "det", "det_picodet_s_320_tflite": "det",
    "pose_superanimal_hrnet_w32": "pose", "pose_superanimal_hrnet_w32_onnx": "pose",
    "pose_rtmpose_s_ap10k": "pose", "pose_rtmpose_m_ap10k": "pose", "pose_rtmpose_m_ap10k_tflite": "pose",
    "pose_rtmpose_ap10k_litert": "pose",
    "face_dog_landmarks_384": "face", "audio_yamnet": "audio",
}

CSV_COLUMNS: tuple[str, ...] = (
    "model", "clip", "frames", "det_recall_vs_ref", "mean_iou_vs_ref",
    *(f"kp_pck@0.1_vs_ref.{n}" for n in CRITICAL_KPS),
    *(f"kp_pck@0.1_vs_ref.{n}" for n in CANONICAL_NAMES if n not in CRITICAL_KPS),
    "kp_pck@0.1_vs_ref", "n_mapped", "unmapped_canonical_kps",
    "tail_tip_visible_rate", "face_ok_rate", "ms_mean_cpu",
    "modality", "license", "artifact_mb", "export_ok", "status", "notes",
)
# The columns the brief names, asserted by tests/data/test_bakeoff_models.py.
REQUIRED_COLUMNS: tuple[str, ...] = (
    "model", "clip", "frames", "det_recall_vs_ref", "mean_iou_vs_ref",
    *(f"kp_pck@0.1_vs_ref.{n}" for n in CANONICAL_NAMES),
    "tail_tip_visible_rate", "face_ok_rate", "ms_mean_cpu",
)


# -- the seam the CLI drives and the tests inject ------------------------------------------------------


class Runner(Protocol):
    """What a candidate can do. A method that does not apply raises, and the bake-off records that."""

    name: str

    def detect(self, frame: np.ndarray) -> tuple[BBox, float] | None: ...
    def pose(self, frame: np.ndarray, bbox: BBox) -> dict[str, Keypoint | None]: ...
    def face(self, frame: np.ndarray, kps: Mapping[str, Keypoint | None]) -> bool: ...
    def audio(self, window: np.ndarray) -> tuple[str, float]: ...


@dataclass(frozen=True)
class Candidate:
    name: str
    modality: str
    license: str
    factory: Callable[[], Runner]


class NullRunner:
    """Stands in for a candidate that cannot be run; every method says why instead of inventing an answer."""

    def __init__(self, name: str, why: str):
        self.name, self.why = name, why
        self.mapping: dict[str, str] = {}
        self.rt = None

    def detect(self, frame): raise NotImplementedError(self.why)
    def pose(self, frame, bbox): raise NotImplementedError(self.why)
    def face(self, frame, kps): raise NotImplementedError(self.why)
    def audio(self, window): raise NotImplementedError(self.why)


# -- geometry ------------------------------------------------------------------------------------------


def iou(a: Sequence[float], b: Sequence[float]) -> float:
    ix = max(0.0, min(a[2], b[2]) - max(a[0], b[0]))
    iy = max(0.0, min(a[3], b[3]) - max(a[1], b[1]))
    inter = ix * iy
    union = (a[2] - a[0]) * (a[3] - a[1]) + (b[2] - b[0]) * (b[3] - b[1]) - inter
    return inter / union if union > 0 else 0.0


def letterbox(im: np.ndarray, size: int, pad: int = 114) -> tuple[np.ndarray, float, tuple[int, int]]:
    """Resize preserving aspect, pad to `size`; returns (image, scale, (left, top)).

    The same geometry as ultralytics' `LetterBox(auto=False, scaleup=True, center=True)`, which is what the
    reference detector uses, reimplemented here so this script does not have to import ultralytics.
    """
    shape = im.shape[:2]
    r = min(size / shape[0], size / shape[1])
    new_unpad = (round(shape[1] * r), round(shape[0] * r))
    dw, dh = (size - new_unpad[0]) / 2, (size - new_unpad[1]) / 2
    top, bottom, left, right = round(dh - 0.1), round(dh + 0.1), round(dw - 0.1), round(dw + 0.1)
    out = cv2.resize(im, new_unpad, interpolation=cv2.INTER_LINEAR) if shape[::-1] != new_unpad else im
    return cv2.copyMakeBorder(out, top, bottom, left, right, cv2.BORDER_CONSTANT, value=(pad,) * 3), r, (left, top)


def unletterbox(box: Sequence[float], scale: float, offset: tuple[int, int]) -> BBox:
    left, top = offset
    return (box[0] / scale, (box[1] - top) / scale, box[2] / scale, (box[3] - top) / scale)


def pick_best(boxes: np.ndarray, scores: np.ndarray) -> int | None:
    """Which box `detect.py` would have picked: highest confidence rounded to 2dp, ties to the larger area.

    Applied verbatim to every candidate (backend/vision/detect.py:139), so recall and IoU compare like with like.
    """
    if not len(scores):
        return None
    area = (boxes[:, 2] - boxes[:, 0]) * (boxes[:, 3] - boxes[:, 1])
    return max(range(len(scores)), key=lambda i: (round(float(scores[i]), 2), float(area[i])))


def nms(boxes: np.ndarray, scores: np.ndarray, thr: float) -> list[int]:
    """Greedy NMS over the boxes, keeping the indices in descending-score order."""
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


IMAGENET_MEAN = np.array([0.485, 0.456, 0.406], np.float32)
IMAGENET_STD = np.array([0.229, 0.224, 0.225], np.float32)


def detector_input(im: np.ndarray, size: int = 320) -> np.ndarray:
    """Letterboxed BGR image -> [1, 3, size, size] float32, RGB /255 (the ultralytics detector input)."""
    rgb = cv2.cvtColor(im, cv2.COLOR_BGR2RGB).astype(np.float32) / 255.0
    return np.ascontiguousarray(rgb.transpose(2, 0, 1)[None], dtype=np.float32)


@dataclass(frozen=True)
class PosePrep:
    """How a crop is mapped into the pose graph's fixed square input, and the inverse map back.

    `sx`/`sy` are *input pixels per crop pixel* and `left`/`top` the pad, so a keypoint read at (x, y) in the
    input's own pixel space lands in the crop at ((x - left) / sx, (y - top) / sy).

      * `stretch` -- fill the square, distorting the aspect ratio: what mmpose does when it maps a bbox to
        256x256, so it is what the RTMPose candidates get.
      * `letterbox` -- scale to fit and reflect-pad the rest (albumentations' `PadIfNeeded` border): the
        fixed-size approximation of what DeepLabCut does for SuperAnimal, which resizes **never** (its
        inference config is `resize=None`, pad to a multiple of 32 and run at native crop size). Measured on
        waiting_corgi over 40 frames it is worth ~4 PCK points over stretch for the SuperAnimal exports, and
        the geometry argument stands on its own: the reference model is never shown a distorted aspect.
    """

    mode: str
    sx: float
    sy: float
    left: float = 0.0
    top: float = 0.0

    def to_crop(self, x: float, y: float) -> tuple[float, float]:
        return ((x - self.left) / self.sx, (y - self.top) / self.sy)


def pose_prep(crop: np.ndarray, mode: str = "stretch", size: int = POSE_SIZE) -> PosePrep:
    """The `PosePrep` for `crop`; both maps round the resize exactly as `pose_input` does, so they invert."""
    h, w = crop.shape[:2]
    if mode == "letterbox":
        r = min(size / h, size / w)
        nw, nh = max(1, round(w * r)), max(1, round(h * r))
        return PosePrep(mode, nw / w, nh / h, float((size - nw) // 2), float((size - nh) // 2))
    return PosePrep(mode, size / w, size / h)


def pose_input(crop: np.ndarray, prep: PosePrep | None = None, size: int = POSE_SIZE) -> np.ndarray:
    """BGR uint8 crop -> [1, 3, size, size] float32, RGB /255 ImageNet-normalised.

    That is DeepLabCut's own inference transform (`A.Normalize(mean=[0.485, 0.456, 0.406], std=[0.229, 0.224, 0.225])`
    over ToFloat, `colormode='RGB'`), so a candidate sees the same normalisation the server's model sees; only the
    geometry differs, which is what `prep` decides (`None` = stretch, the historical default).
    """
    prep = prep or pose_prep(crop, "stretch", size)
    if prep.mode == "letterbox":
        nw, nh = int(round(prep.sx * crop.shape[1])), int(round(prep.sy * crop.shape[0]))
        resized = cv2.resize(crop, (nw, nh), interpolation=cv2.INTER_LINEAR)
        top, left = int(round(prep.top)), int(round(prep.left))
        image = cv2.copyMakeBorder(resized, top, size - nh - top, left, size - nw - left,
                                   cv2.BORDER_REFLECT_101)  # albumentations' PadIfNeeded border_mode=4
    else:
        image = cv2.resize(crop, (size, size), interpolation=cv2.INTER_LINEAR)
    rgb = cv2.cvtColor(image, cv2.COLOR_BGR2RGB)
    x = ((rgb.astype(np.float32) / 255.0) - IMAGENET_MEAN) / IMAGENET_STD
    return np.ascontiguousarray(x.transpose(2, 0, 1)[None], dtype=np.float32)


def softmax(x: np.ndarray, axis: int) -> np.ndarray:
    e = np.exp(x - x.max(axis=axis, keepdims=True))
    return e / e.sum(axis=axis, keepdims=True)


# -- runtimes ------------------------------------------------------------------------------------------


class Tflite:
    """A LiteRT interpreter on one thread, with the model time separated from preprocessing."""

    def __init__(self, path: Path, threads: int = THREADS):
        from ai_edge_litert import interpreter as itp

        self.path = path
        self.interp = itp.Interpreter(model_path=str(path), num_threads=threads)
        self.interp.allocate_tensors()
        self.inp = self.interp.get_input_details()[0]
        self.outs = self.interp.get_output_details()
        self.ms: list[float] = []
        self.warm = True

    def __call__(self, x: np.ndarray) -> list[np.ndarray]:
        t = time.perf_counter()
        self.interp.set_tensor(self.inp["index"], np.ascontiguousarray(x.astype(self.inp["dtype"])))
        self.interp.invoke()
        res = [np.array(self.interp.get_tensor(o["index"])) for o in self.outs]
        ms = (time.perf_counter() - t) * 1000
        if self.warm:  # the first invoke pays delegate setup; the server excludes its first call the same way
            self.warm = False
        else:
            self.ms.append(ms)
        return res

    def reset_timing(self) -> None:
        self.ms.clear()

    @property
    def mean_ms(self) -> float | None:
        return sum(self.ms) / len(self.ms) if self.ms else None


class Onnx:
    """An onnxruntime session on one thread, timed the same way."""

    def __init__(self, path: Path):
        import onnxruntime as ort

        self.path = path
        opts = ort.SessionOptions()
        opts.intra_op_num_threads = THREADS
        opts.inter_op_num_threads = THREADS
        opts.log_severity_level = 3
        self.sess = ort.InferenceSession(str(path), opts, providers=["CPUExecutionProvider"])
        self.inp = self.sess.get_inputs()[0]
        self.ms: list[float] = []
        self.warm = True

    def __call__(self, x: np.ndarray) -> list[np.ndarray]:
        t = time.perf_counter()
        res = self.sess.run(None, {self.inp.name: np.ascontiguousarray(x.astype(np.float32))})
        ms = (time.perf_counter() - t) * 1000
        if self.warm:
            self.warm = False
        else:
            self.ms.append(ms)
        return list(res)

    reset_timing = Tflite.reset_timing
    mean_ms = Tflite.mean_ms


def load_runtime(path: Path) -> Tflite | Onnx:
    return Onnx(path) if path.suffix == ".onnx" else Tflite(path)


# -- pose decoders -------------------------------------------------------------------------------------


def heatmap_keypoints(
    heat: np.ndarray,
    names: Sequence[str],
    mapping: Mapping[str, str],
    crop: np.ndarray,
    offset: tuple[int, int],
    prep: PosePrep | None = None,
) -> dict[str, Keypoint | None]:
    """Argmax per heatmap -> canonical keypoints in full-frame pixels.

    `heat` is [K, grid, grid] over the pose input's square: a cell is POSE_SIZE/grid pixels of the *input*, which
    `prep` maps into the crop, which `offset` maps into the frame. The confidence is the heatmap peak, as
    DeepLabCut's predictor reads it (its config is `apply_sigmoid=False, clip_scores=True`, so the raw peak is the
    score), and points outside the crop by more than the margin are dropped for the same reason `pose.py` drops them.
    """
    prep = prep or pose_prep(crop, "stretch")
    h, w = crop.shape[:2]
    grid = heat.shape[-1]
    cell = POSE_SIZE / grid
    flat = heat.reshape(heat.shape[0], -1)
    ys, xs = np.divmod(flat.argmax(axis=1), grid)
    mx, my = OUTSIDE_MARGIN_FRAC * w, OUTSIDE_MARGIN_FRAC * h
    ox, oy = offset
    out: dict[str, Keypoint | None] = {n: None for n in CANONICAL_NAMES}
    for i, raw in enumerate(names):
        canon = mapping.get(raw)
        if canon is None:
            continue
        cx, cy = prep.to_crop(float(xs[i]) * cell, float(ys[i]) * cell)
        c = float(flat[i, int(ys[i]) * grid + xs[i]])
        if c < KPT_CONF_THR or not (-mx <= cx <= w + mx) or not (-my <= cy <= h + my):
            continue
        out[canon] = (cx + ox, cy + oy, c)
    return out


def simcc_keypoints(
    simcc_x: np.ndarray,
    simcc_y: np.ndarray,
    names: Sequence[str],
    mapping: Mapping[str, str],
    crop: np.ndarray,
    offset: tuple[int, int],
    prep: PosePrep | None = None,
) -> dict[str, Keypoint | None]:
    """RTMPose's SimCC head -> canonical keypoints in full-frame pixels.

    Each axis is a 2*size-long logit vector (split ratio 2), so a coordinate is `argmax / 2` in the input's own
    pixel space -- the same decode DLC's `SimCCPredictor` runs (`keypoints /= simcc_split_ratio`, checked against
    it on a real frame) -- which `prep` then maps into the crop. mmdeploy's score is `max(x_part) * max(y_part)`.
    """
    prep = prep or pose_prep(crop, "stretch")
    h, w = crop.shape[:2]
    size = simcc_x.shape[-1] // 2  # the vector's own pixel space; only equal to POSE_SIZE for a 256 model
    k = POSE_SIZE / size
    ix, iy = simcc_x.argmax(axis=-1), simcc_y.argmax(axis=-1)
    xs = ix / 2.0 * k
    ys = iy / 2.0 * k
    score = simcc_x.max(axis=-1) * simcc_y.max(axis=-1)
    ox, oy = offset
    mx, my = OUTSIDE_MARGIN_FRAC * w, OUTSIDE_MARGIN_FRAC * h
    out: dict[str, Keypoint | None] = {n: None for n in CANONICAL_NAMES}
    for i, raw in enumerate(names):
        canon = mapping.get(raw)
        if canon is None:
            continue
        cx, cy = prep.to_crop(float(xs[i]), float(ys[i]))
        c = float(score[i])
        if c < KPT_CONF_THR or not (-mx <= cx <= w + mx) or not (-my <= cy <= h + my):
            continue
        out[canon] = (cx + ox, cy + oy, c)
    return out


# -- detector postprocessors ---------------------------------------------------------------------------
#
# Each of these was checked against the reference box before being written down; the class docstring says what the
# graph actually outputs, which is not always what the model card implies.


class YoloEnd2end:
    """det_yolo26n_320_fp32: ultralytics' end2end LiteRT export, [1, 300, 6] = (x1, y1, x2, y2, conf, class).

    NMS is already in the graph and the boxes are in the letterboxed 320 frame.
    """

    dog = DOG_COCO

    def __init__(self, rt: Tflite):
        self.rt = rt

    def boxes(self, frame: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
        lb, r, off = letterbox(frame, 320)
        out = np.asarray(self.rt(detector_input(lb, 320))[0]).reshape(-1, 6)
        keep = np.flatnonzero((out[:, 5] == self.dog) & (out[:, 4] >= CONF_THR))
        if keep.size == 0:
            return np.zeros((0, 4)), np.zeros((0,))
        return np.array([unletterbox(out[i, :4], r, off) for i in keep]), out[keep, 4]


class YoloRawHead:
    """det_yolo26n_320_int8: [1, 84, 2100] = (cx, cy, w, h, 80 classes) with boxes normalised to the letterbox.

    The class head leaves the graph clipped to a 0..0.5 range at 1/256 resolution, so every confident prediction reads
    0.499: the boxes are usable, the confidences are not comparable with the fp32 export's and cannot rank boxes.
    """

    dog = DOG_COCO

    def __init__(self, rt: Tflite):
        self.rt = rt

    def boxes(self, frame: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
        lb, r, off = letterbox(frame, 320)
        out = np.asarray(self.rt(detector_input(lb, 320))[0]).reshape(84, -1)
        scores = out[4 + self.dog]
        keep = np.flatnonzero(scores >= CONF_THR)
        if keep.size == 0:
            return np.zeros((0, 4)), np.zeros((0,))
        cx, cy, w, h = out[0, keep], out[1, keep], out[2, keep], out[3, keep]
        px = np.stack([cx - w / 2, cy - h / 2, cx + w / 2, cy + h / 2], axis=1) * 320.0
        return np.array([unletterbox(b, r, off) for b in px]), scores[keep]


class PicoDet:
    """det_picodet_s_320(.onnx/.tflite): PP-PicoDet's DFL head, four levels, and no NMS in the graph.

    Checked against the reference box before being written here: the reg head's 32 channels are 8 DFL bins x 4 corners
    in `(y1, x1, y2, x2)` order, bin-major, each distance being the softmax expectation over the bins times the
    stride, and the anchors are cell centres in row-major order. Input preprocessing is letterbox + ImageNet
    normalisation, which is the variant with the highest dog score (0.80 against 0.24 for /255 alone).
    """

    STRIDES = (8, 16, 32, 64)
    REG_MAX = 7

    def __init__(self, rt: Tflite | Onnx):
        self.rt = rt

    def boxes(self, frame: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
        lb, r, off = letterbox(frame, 320)
        rgb = cv2.cvtColor(lb, cv2.COLOR_BGR2RGB).astype(np.float32) / 255.0
        x = np.ascontiguousarray(((rgb - IMAGENET_MEAN) / IMAGENET_STD).transpose(2, 0, 1)[None], np.float32)
        # the converted tflite took its input in NHWC; the onnx wants NCHW, so match the graph
        shape = getattr(self.rt.inp, "shape", None) or self.rt.inp.get("shape")
        if shape is not None and len(shape) == 4 and shape[1] not in (3, "3") and isinstance(shape[1], int):
            x = np.ascontiguousarray(x.transpose(0, 2, 3, 1))
        outs = self.rt(x)
        cls_out = [o for o in outs if o.shape[-1] == 80]
        reg_out = [o for o in outs if o.shape[-1] == 4 * (self.REG_MAX + 1)]
        bins = np.arange(self.REG_MAX + 1, dtype=np.float32)
        boxes: list[BBox] = []
        scores: list[float] = []
        for stride, cls, reg in zip(self.STRIDES, cls_out, reg_out):
            cls = np.asarray(cls).reshape(-1, 80)
            side = int(round(cls.shape[0] ** 0.5))
            probs = softmax(np.asarray(reg).reshape(-1, self.REG_MAX + 1, 4), axis=1)
            dist = (probs * bins[None, :, None]).sum(axis=1) * stride
            yy, xx = np.meshgrid(np.arange(side), np.arange(side), indexing="ij")
            cx = (xx.reshape(-1) + 0.5) * stride
            cy = (yy.reshape(-1) + 0.5) * stride
            cand = np.stack([cx - dist[:, 1], cy - dist[:, 0], cx + dist[:, 3], cy + dist[:, 2]], axis=1)
            s = cls[:, DOG_COCO]
            above = np.nonzero(s >= CONF_THR)[0]
            if len(above):
                cand_above = cand[above]
                s_above = s[above]
                for i in nms(cand_above, s_above, 0.5):
                    boxes.append(unletterbox(cand_above[i], r, off))
                    scores.append(float(s_above[i]))
        if not boxes:
            return np.zeros((0, 4)), np.zeros((0,))
        return np.array(boxes), np.array(scores)


class EffDetLite:
    """det_effdet_lite0_320_int8: MediaPipe's EfficientDet-lite0 **raw head** -- no decode, no NMS in the graph.

    [1, 19206, 90] of class probabilities over MediaPipe's 90 classes (dog is 17, not COCO's 16) and [1, 19206, 4] of
    `(dc_y, dc_x, dh, dw)` deltas. 19206 = (40^2 + 20^2 + 10^2 + 5^2 + 3^2) locations x 9 anchors, so the anchor
     table is not in the artifact: `--fit-effdet-anchors` solves it from the model's own box predictions on dog
     frames and writes `effdet_anchor_table.json` next to it (unsolved anchors fall back to their level's mean).
     A phone would have to ship that table too.
    """

    LEVELS = ((8, 40), (16, 20), (32, 10), (64, 5), (128, 3))
    dog = DOG_MEDIAPIPE
    TABLE_NAME = "effdet_anchor_table.json"

    def __init__(self, rt: Tflite, table: Mapping[str, Sequence[float]]):
        self.rt = rt
        self.table = {k: tuple(v) for k, v in table.items()}
        self.cx, self.cy, self.keys = self.anchor_grid()
        from collections import defaultdict
        by_level: dict[int, list[float]] = defaultdict(lambda: [0.0, 0.0, 0])
        for key, (ta, tw) in self.table.items():
            stride = int(key.split("_")[0])
            by_level[stride][0] += ta
            by_level[stride][1] += tw
            by_level[stride][2] += 1
        level_mean = {s: (v[0] / v[2], v[1] / v[2]) for s, v in by_level.items() if v[2]}

        def prior(s: int, k: int) -> tuple[float, float]:
            hit = self.table.get(f"{s}_{k}")
            return (hit[0], hit[1]) if hit is not None else level_mean.get(s, (float(s), float(s)))

        self.ah = np.array([prior(s, k)[0] for s, k in self.keys], np.float32)
        self.aw = np.array([prior(s, k)[1] for s, k in self.keys], np.float32)

    def anchor_grid(self) -> tuple[np.ndarray, np.ndarray, list[tuple[int, int]]]:
        """(cx, cy, per-anchor (stride, k)) for the concatenated 19 206 rows, in the graph's own order."""
        n = sum(side * side * 9 for _, side in self.LEVELS)
        cx = np.zeros(n)
        cy = np.zeros(n)
        keys: list[tuple[int, int]] = []
        off = 0
        for stride, side in self.LEVELS:
            yy, xx = np.meshgrid(np.arange(side), np.arange(side), indexing="ij")
            for k in range(9):
                rows = off + (yy.reshape(-1) * side + xx.reshape(-1)) * 9 + k
                cx[rows] = (xx.reshape(-1) + 0.5) * stride
                cy[rows] = (yy.reshape(-1) + 0.5) * stride
                keys.extend([(stride, k)] * rows.size)
            off += side * side * 9
        return cx, cy, keys

    def boxes(self, frame: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
        h, w = frame.shape[:2]
        rgb = cv2.cvtColor(cv2.resize(frame, (320, 320), interpolation=cv2.INTER_LINEAR), cv2.COLOR_BGR2RGB)
        outs = self.rt(np.ascontiguousarray(rgb[None], dtype=np.uint8))
        cls = np.asarray(next(o for o in outs if o.shape[-1] > 4)[0])
        box = np.asarray(next(o for o in outs if o.shape[-1] == 4)[0])
        s = cls[:, self.dog]
        above = np.nonzero(s >= CONF_THR)[0]
        if not len(above):
            return np.zeros((0, 4)), np.zeros((0,))

        y = self.cy[above] + box[above, 0] * self.ah[above]
        x = self.cx[above] + box[above, 1] * self.aw[above]
        hh = np.exp(np.clip(box[above, 2], -8, 8)) * self.ah[above]
        ww = np.exp(np.clip(box[above, 3], -8, 8)) * self.aw[above]
        cand = np.stack([x - ww, y - hh, x + ww, y + hh], axis=1)
        cand[:, [0, 2]] *= w / 320.0
        cand[:, [1, 3]] *= h / 320.0
        s_above = s[above]
        keep = nms(cand, s_above, 0.5)
        if not len(keep):
            return np.zeros((0, 4)), np.zeros((0,))
        return cand[keep], s_above[keep]


# -- candidate runners ---------------------------------------------------------------------------------


class DetectorRunner:
    """Wraps a decoder so every detector candidate selects its box the way `detect.py` does."""

    def __init__(self, name: str, decode: Any):
        self.name = name
        self.decode = decode
        self.rt = decode.rt
        self.mapping: dict[str, str] = {}

    def detect(self, frame: np.ndarray) -> tuple[BBox, float] | None:
        boxes, scores = self.decode.boxes(frame)
        i = pick_best(boxes, scores)
        return None if i is None else (tuple(float(v) for v in boxes[i]), float(scores[i]))

    def pose(self, frame, bbox):
        raise NotImplementedError(f"{self.name} is a detector, not a pose model")

    def face(self, frame, kps):
        raise NotImplementedError(f"{self.name} is a detector, not a face model")

    def audio(self, window):
        raise NotImplementedError(f"{self.name} is a detector, not an audio model")


class PoseRunner:
    """A pose candidate: crop the given box, run the exported graph, map to canonical names."""

    def __init__(self, name: str, rt: Tflite | Onnx, names: Sequence[str], mapping: Mapping[str, str], kind: str,
                 prep_mode: str = "stretch"):
        self.name = name
        self.rt = rt
        self.names = tuple(names)
        self.mapping = dict(mapping)
        self.kind = kind
        self.prep_mode = prep_mode

    def detect(self, frame):
        raise NotImplementedError(f"{self.name} is a pose model, not a detector")

    def pose(self, frame: np.ndarray, bbox: BBox) -> dict[str, Keypoint | None]:
        crop, off = crop_padded(frame, bbox, CROP_PAD)
        if crop.shape[0] < 2 or crop.shape[1] < 2:
            return {n: None for n in CANONICAL_NAMES}
        prep = pose_prep(crop, self.prep_mode)
        outs = self.rt(pose_input(crop, prep))
        if self.kind == "heatmap":
            heat = np.asarray(outs[0], np.float32)
            return heatmap_keypoints(heat[0] if heat.ndim == 4 else heat, self.names, self.mapping, crop, off, prep)
        x, y = np.asarray(outs[0], np.float32), np.asarray(outs[1], np.float32)
        return simcc_keypoints(x[0] if x.ndim == 3 else x, y[0] if y.ndim == 3 else y,
                               self.names, self.mapping, crop, off, prep)

    def face(self, frame, kps):
        raise NotImplementedError(f"{self.name} is a pose model; the face is its own candidate")

    def audio(self, window):
        raise NotImplementedError(f"{self.name} is a pose model, not an audio model")


class FaceRunner:
    """The DogFLW landmark model as the phone would run it: the exact exported .tflite behind `FaceLandmarker`."""

    def __init__(self, name: str, rt: Tflite, cfg: Mapping[str, Any]):
        from backend.vision.face import FaceLandmarker

        self.name = name
        self.rt = rt
        self.mapping: dict[str, str] = {}
        self.landmarker = FaceLandmarker(dict(cfg), runner=lambda x: rt(x)[0][0])

    def detect(self, frame):
        raise NotImplementedError(f"{self.name} is a face model, not a detector")

    def pose(self, frame, bbox):
        raise NotImplementedError(f"{self.name} is a face model, not a pose model")

    def face(self, frame: np.ndarray, kps: Mapping[str, Keypoint | None]) -> bool:
        return self.landmarker.estimate(frame, dict(kps)) is not None

    def audio(self, window):
        raise NotImplementedError(f"{self.name} is a face model, not an audio model")


class AudioRunner:
    """YAMNet as the phone would run it: the MediaPipe .tflite on the same 15 600-sample windows."""

    def __init__(self, name: str, rt: Tflite, label_idx: Mapping[str, list[int]], threshold: float):
        self.name = name
        self.rt = rt
        self.label_idx = {k: list(v) for k, v in label_idx.items()}
        self.threshold = threshold
        self.mapping: dict[str, str] = {}
        mapped = {i for idx in self.label_idx.values() for i in idx}
        self.other_idx = np.array([i for i in range(521) if i not in mapped])

    def label(self, window: np.ndarray) -> tuple[str, float]:
        scores = np.asarray(self.rt(np.asarray(window, np.float32))[0]).reshape(-1)
        per = {lab: float(scores[idx].max()) for lab, idx in self.label_idx.items()}
        best = max(per, key=per.get)
        if best is not None and per[best] >= self.threshold:
            return best, float(np.clip(per[best], 0.0, 1.0))
        return "other", float(np.clip(scores[self.other_idx].max(), 0.0, 1.0))

    def detect(self, frame):
        raise NotImplementedError(f"{self.name} is an audio model, not a detector")

    def pose(self, frame, bbox):
        raise NotImplementedError(f"{self.name} is an audio model, not a pose model")

    def face(self, frame, kps):
        raise NotImplementedError(f"{self.name} is an audio model, not a face model")

    def audio(self, window: np.ndarray) -> tuple[str, float]:
        return self.label(window)


# -- the reference: the server stack, untouched --------------------------------------------------------


class ServerStack:
    """YOLO11s@640 + SuperAnimal-Quadruped HRNet-W32 + DogFLW + YAMNet, driven as the pipeline drives them.

    This is what `scripts/precompute_events.py` runs internally, minus the rules and the events file: the same
    `DogDetector`, `PoseEstimator`, `FaceLandmarker` and `AudioEventDetector` on the same 8 fps frames.
    """

    name = "server_reference"

    def __init__(self, cfg: Mapping[str, Any], device: str = "cpu", with_audio: bool = True):
        import warnings

        from backend.audio.yamnet_events import AudioEventDetector
        from backend.vision.detect import DogDetector
        from backend.vision.face import FaceLandmarker
        from backend.vision.pose import PoseEstimator

        self._detector = DogDetector(dict(cfg), device=device)
        self._pose = PoseEstimator(dict(cfg), device=device)
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            self._face = FaceLandmarker(dict(cfg))
        self.audio_det = AudioEventDetector(dict(cfg)) if with_audio else None
        self.ms: list[float] = []
        self.mapping = {n: n for n in CANONICAL_NAMES}

    # -- the Runner interface, so the scoring loop does not care who is who --------------------
    def detect(self, frame: np.ndarray) -> tuple[BBox, float] | None:
        t = time.perf_counter()
        d = self._detector.detect(frame)
        self.ms.append((time.perf_counter() - t) * 1000)
        return None if d is None else (d.raw_bbox, d.conf)

    def pose(self, frame: np.ndarray, bbox: BBox) -> dict[str, Keypoint | None]:
        t = time.perf_counter()
        kps = self._pose.estimate(frame, bbox)
        self.ms.append((time.perf_counter() - t) * 1000)
        return kps

    def face(self, frame: np.ndarray, kps: Mapping[str, Keypoint | None]) -> bool:
        t = time.perf_counter()
        ok = self._face.estimate(frame, dict(kps)) is not None
        self.ms.append((time.perf_counter() - t) * 1000)
        return ok

    def audio(self, window: np.ndarray) -> tuple[str, float]:
        return self.audio_label(window)

    # -- audio ----------------------------------------------------------------------------
    def label_map(self) -> tuple[dict[str, list[int]], float]:
        a = self.audio_det
        return {k: list(v) for k, v in a._label_idx.items()}, a.threshold

    def audio_label(self, window: np.ndarray) -> tuple[str, float]:
        """The reference's own label for one window, reusing its silence gate and its label map."""
        a = self.audio_det
        w = np.asarray(window, np.float32)
        rms = float(np.sqrt(np.mean(w.astype(np.float64) ** 2)))
        if rms < a.silence_rms:
            return "silence", float(np.clip(1.0 - rms / a.silence_rms, 0.0, 1.0))
        scores = np.asarray(a._model(w)[0])
        scores = scores.max(axis=0) if scores.ndim == 2 else scores
        per = {lab: float(scores[idx].max()) for lab, idx in a._label_idx.items()}
        best = max(per, key=per.get)
        if best is not None and per[best] >= a.threshold:
            return best, float(np.clip(per[best], 0.0, 1.0))
        return "other", float(np.clip(scores[a._other_idx].max(), 0.0, 1.0))


# -- the measurement ------------------------------------------------------------------------------------


@dataclass
class ClipFrames:
    name: str
    frames: list[np.ndarray] = field(default_factory=list)
    windows: list[np.ndarray] = field(default_factory=list)


@dataclass
class RefFrame:
    box: BBox | None = None
    conf: float = 0.0
    kps: dict[str, Keypoint | None] = field(default_factory=dict)
    face_ok: bool = False


def read_clip(path: Path, fps: float, limit: int | None = None) -> ClipFrames:
    """Frames and audio windows for one clip, on the clock `MediaSources` gives the pipeline."""
    from backend.sources import MediaSources

    out = ClipFrames(name=path.stem)
    with MediaSources(type="file", path=path, fps=fps, fast=True) as src:
        for _, frame in src.video():
            if limit is not None and len(out.frames) >= limit:
                break
            out.frames.append(frame)
    if limit is not None:
        return out
    try:
        with MediaSources(type="file", path=path, fps=fps, fast=True) as src:
            buf: list[np.ndarray] = []
            for _, chunk in src.audio():
                buf.append(np.asarray(chunk, np.float32))
                if sum(len(c) for c in buf) >= YAMNET_WINDOW:
                    cat = np.concatenate(buf)
                    out.windows.append(cat[:YAMNET_WINDOW].copy())
                    rest = cat[YAMNET_WINDOW:]
                    buf = [rest] if len(rest) else []
    except (RuntimeError, OSError) as exc:
        # A clip with no audio track must not lose the video measurements; the audio columns go blank instead.
        log.warning("%s: no audio windows (%s)", path.stem, exc)
    return out


def reference_passes(clip: ClipFrames, server: ServerStack) -> list[RefFrame]:
    """One pass of the server stack over a clip: the box, the keypoints from that box, the face from those."""
    refs = []
    for frame in clip.frames:
        det = server.detect(frame)
        kps: dict[str, Keypoint | None] = {}
        face_ok = False
        if det is not None:
            kps = server.pose(frame, det[0])
            face_ok = server.face(frame, kps)
        refs.append(RefFrame(None if det is None else det[0], 0.0 if det is None else det[1], kps, face_ok))
    return refs


def pck(candidate: Keypoint | None, ref: Keypoint | None, radius: float) -> float | None:
    """1.0 inside `radius` px, else 0.0; None when the candidate has no point (so a miss is a miss, not a gap)."""
    if candidate is None or ref is None:
        return None
    return 1.0 if math.hypot(candidate[0] - ref[0], candidate[1] - ref[1]) <= radius else 0.0


def _round(value: float | None, digits: int = 4) -> Any:
    return "" if value is None else round(value, digits)


def bakeoff_clip(
    candidate: Candidate,
    runner: Runner | None,
    clip: ClipFrames,
    refs: Sequence[RefFrame],
    server: ServerStack,
    *,
    status: str,
    notes: str,
    artifact_mb: float | None,
    export_ok: bool,
) -> dict[str, Any]:
    """One CSV row: the candidate against the reference on one clip. A failure is a row, not a crash."""
    row: dict[str, Any] = {
        "model": candidate.name, "clip": clip.name, "frames": len(clip.frames), "modality": candidate.modality,
        "license": candidate.license, "artifact_mb": artifact_mb if artifact_mb is not None else "",
        "export_ok": export_ok, "status": status, "notes": notes,
    }
    for name in CANONICAL_NAMES:
        row[f"kp_pck@0.1_vs_ref.{name}"] = ""
    row["kp_pck@0.1_vs_ref"] = ""
    row["n_mapped"] = ""
    row["unmapped_canonical_kps"] = ""
    for col in ("det_recall_vs_ref", "mean_iou_vs_ref", "tail_tip_visible_rate", "face_ok_rate", "ms_mean_cpu",
                "audio_label_match_vs_ref", "audio_windows"):
        row[col] = ""
    if runner is None:
        return row

    modality = candidate.modality
    hits = {n: 0.0 for n in CANONICAL_NAMES}
    seen = {n: 0 for n in CANONICAL_NAMES}
    ref_found = found = iou_sum = 0
    posed = tail_tip = face_ok = face_n = 0
    try:
        for frame, ref in zip(clip.frames, refs):
            if modality == "det":
                det = runner.detect(frame)
                if ref.box is None:
                    continue
                ref_found += 1
                if det is not None:
                    found += 1
                    iou_sum += iou(det[0], ref.box)
                continue
            if modality == "audio":
                continue  # audio is scored window-by-window below, not per frame
            if ref.box is None:
                continue
            if modality == "face":
                face_n += 1
                face_ok += bool(runner.face(frame, ref.kps))
                continue
            kps = runner.pose(frame, ref.box)
            posed += 1
            tail_tip += kps.get("tail_tip") is not None
            radius = PCK_THRESH * math.hypot(ref.box[2] - ref.box[0], ref.box[3] - ref.box[1])
            for name in CANONICAL_NAMES:
                if ref.kps.get(name) is None:
                    continue
                seen[name] += 1
                got = pck(kps.get(name), ref.kps[name], radius)
                if got is not None:
                    hits[name] += got
        if modality == "audio" and server.audio_det is not None:
            label_map, thr = server.label_map()
            runner.label_idx, runner.threshold = label_map, thr
            n = same = 0
            for window in clip.windows:
                ref_label, _ = server.audio_label(window)
                if ref_label == "silence":
                    continue  # the reference never ran the model on a silent window; nothing to compare
                n += 1
                same += runner.audio(window)[0] == ref_label
            row["audio_label_match_vs_ref"] = _round(same / n) if n else ""
            row["audio_windows"] = n
    except Exception as exc:  # noqa: BLE001  (a candidate that breaks is a recorded result, not a lost run)
        row["status"] = f"failed: {type(exc).__name__}: {exc}"[:300]
        row["notes"] = f"{notes}; raised {type(exc).__name__} after {len(clip.frames)} frames".strip("; ")
        return row

    if modality == "det":
        row["det_recall_vs_ref"] = _round(found / ref_found) if ref_found else ""
        row["mean_iou_vs_ref"] = _round(iou_sum / ref_found) if ref_found else ""
    if modality == "pose":
        mapped = [n for n in CANONICAL_NAMES if n in runner.mapping.values()]
        for name in CANONICAL_NAMES:
            if name in mapped and seen[name]:
                row[f"kp_pck@0.1_vs_ref.{name}"] = _round(hits[name] / seen[name])
        acc = [hits[n] / seen[n] for n in mapped if seen[n]]
        row["kp_pck@0.1_vs_ref"] = _round(sum(acc) / len(acc)) if acc else ""
        row["n_mapped"] = len(mapped)
        row["unmapped_canonical_kps"] = " ".join(n for n in CANONICAL_NAMES if n not in mapped)
        row["tail_tip_visible_rate"] = _round(tail_tip / posed) if posed else ""
    if modality == "face":
        row["face_ok_rate"] = _round(face_ok / face_n) if face_n else ""
    rt = getattr(runner, "rt", None)
    if rt is not None and getattr(rt, "mean_ms", None) is not None:
        row["ms_mean_cpu"] = round(rt.mean_ms, 2)
    return row


# -- CSV -----------------------------------------------------------------------------------------------


def write_csv(rows: Sequence[Mapping[str, Any]], path: Path) -> Path:
    columns = list(CSV_COLUMNS)
    for row in rows:
        for key in row:
            if key not in columns:
                columns.append(key)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=columns, extrasaction="ignore")
        writer.writeheader()
        for row in rows:
            writer.writerow({c: row.get(c, "") for c in columns})
    return path


def summarise(rows: Sequence[Mapping[str, Any]]) -> None:
    print("\nmodel                             det_recall  mean_iou   kp_pck   tail_tip  face_ok  ms_mean  status")
    for model in dict.fromkeys(r["model"] for r in rows):
        got = [r for r in rows if r["model"] == model]
        def mean(col: str, digits: int = 3) -> str:
            vals = [r[col] for r in got if isinstance(r.get(col), float) or (isinstance(r.get(col), int) and col == "frames")]
            return f"{sum(vals)/len(vals):.{digits}f}" if vals else "-"
        print(f"  {model:32s} {mean('det_recall_vs_ref'):>9s}  {mean('mean_iou_vs_ref'):>8s}  "
              f"{mean('kp_pck@0.1_vs_ref'):>6s}  {mean('tail_tip_visible_rate'):>8s}  "
              f"{mean('face_ok_rate'):>7s}  {mean('ms_mean_cpu', 1):>7s}  {got[0]['status'][:40]}")


# -- wiring --------------------------------------------------------------------------------------------


def candidates_from_report(
    report: Mapping[str, Any],
    export_dir: Path,
    cfg: Mapping[str, Any],
    builders: Mapping[str, Callable[[], Runner]] | None = None,
) -> list[Candidate]:
    """Task 0.1's candidate set, in its order, each wired to a runner over its exact exported file.

    `builders` replaces the whole registry (the tests do). A name it does not cover falls back to the real loader,
    which is what makes "the CSV has a row for every candidate" a property of this function and not of the CLI.
    """
    import scripts.export_android_models as E

    names = [spec.name for spec in E.CANDIDATES]
    registry: dict[str, Callable[[], Runner]] = dict(builders or {})
    if any(n not in registry for n in names):
        # Only pay for the real loaders (and for importing deeplabcut for the SuperAnimal bodypart order) when a
        # candidate is not already covered, so a test that stubs the whole set stays cheap.
        registry = {**default_builders(export_dir, report, cfg), **registry}
    out = []
    for spec in E.CANDIDATES:
        build = registry.get(spec.name)
        factory = build if build is not None else (lambda s=spec: NullRunner(s.name, f"{s.name} has no runner"))
        out.append(Candidate(spec.name, MODALITY.get(spec.name, "pose"), spec.license, factory))
    return out


def default_builders(
    export_dir: Path, report: Mapping[str, Any], cfg: Mapping[str, Any]
) -> dict[str, Callable[[], Runner]]:
    """name -> factory over the exact exported artifact `export_report.json` records."""
    import scripts.export_android_models as E

    def path_of(name: str) -> Path:
        entry = report.get(name)
        if not entry or not entry.get("path"):
            raise FileNotFoundError(f"{name} was not exported (Task 0.1 recorded export_ok=false)")
        return Path(entry["path"])

    def det(name: str, decode_cls: type) -> Callable[[], Runner]:
        def build() -> Runner:
            return DetectorRunner(name, decode_cls(load_runtime(path_of(name))))
        return build

    def pose(name: str, names: Sequence[str], mapping: Mapping[str, str], kind: str) -> Callable[[], Runner]:
        def build() -> Runner:
            return PoseRunner(name, load_runtime(path_of(name)), names, mapping, kind)
        return build

    def face(name: str) -> Callable[[], Runner]:
        def build() -> Runner:
            return FaceRunner(name, Tflite(path_of(name)), cfg)
        return build

    def audio(name: str) -> Callable[[], Runner]:
        def build() -> Runner:
            rt = Tflite(path_of(name))
            index = {n: i for i, n in enumerate(yamnet_class_names())}
            audio_cfg = cfg.get("data", {}).get("audio", {})
            label_map = audio_cfg.get("label_map") or {
                "bark": ["Bark", "Bow-wow"], "yip": ["Yip"], "growl": ["Growling"],
                "whimper": ["Whimper (dog)"], "howl": ["Howl"],
            }
            label_idx = {lab: [index[n] for n in names] for lab, names in label_map.items()}
            return AudioRunner(name, rt, label_idx, float(audio_cfg.get("threshold", 0.3)))
        return build

    def effdet() -> Callable[[], Runner]:
        def build() -> Runner:
            path = path_of("det_effdet_lite0_320_int8")
            table_path = path.parent / EffDetLite.TABLE_NAME
            if not table_path.is_file():
                raise FileNotFoundError(
                    f"{table_path} is missing: the artifact carries no anchor table and no NMS, so run "
                    "`python scripts/bakeoff_models.py --fit-effdet-anchors` first"
                )
            return DetectorRunner("det_effdet_lite0_320_int8",
                                  EffDetLite(Tflite(path), json.loads(table_path.read_text())))
        return build

    def superanimal(name: str) -> Callable[[], Runner]:
        def build() -> Runner:
            # the 39 bodypart names in the model's own output order are read from its config, not guessed;
            # prep_mode="letterbox" because SuperAnimal's own inference never distorts the aspect ratio
            # (DeepLabCut `resize=None` + pad-to-/32 at native crop size) -- see PosePrep.
            return PoseRunner(name, load_runtime(path_of(name)), _superanimal_bodyparts("hrnet_w32"),
                              SUPERANIMAL_TO_CANONICAL, "heatmap", prep_mode="letterbox")
        return build

    builders: dict[str, Callable[[], Runner]] = {
        "det_yolo26n_320_fp32": det("det_yolo26n_320_fp32", YoloEnd2end),
        "det_yolo26n_320_int8": det("det_yolo26n_320_int8", YoloRawHead),
        "det_picodet_s_320": det("det_picodet_s_320", PicoDet),
        "det_picodet_s_320_tflite": det("det_picodet_s_320_tflite", PicoDet),
        "det_effdet_lite0_320_int8": effdet(),
        "face_dog_landmarks_384": face("face_dog_landmarks_384"),
        "audio_yamnet": audio("audio_yamnet"),
        "pose_superanimal_hrnet_w32": superanimal("pose_superanimal_hrnet_w32"),
        "pose_superanimal_hrnet_w32_onnx": superanimal("pose_superanimal_hrnet_w32_onnx"),
    }
    for name in ("pose_rtmpose_s_ap10k", "pose_rtmpose_m_ap10k", "pose_rtmpose_m_ap10k_tflite",
                 "pose_rtmpose_ap10k_litert"):
        builders[name] = pose(name, AP10K_NAMES, AP10K_TO_CANONICAL, "simcc")
    return builders


def yamnet_class_names() -> list[str]:
    """The 521 AudioSet display names YAMNet's output vector is indexed by (fetched once, then offline)."""
    from backend.audio.yamnet_events import load_yamnet

    _, names = load_yamnet(str(ROOT / "models" / "yamnet"))
    return list(names)


def _superanimal_bodyparts(model_name: str) -> tuple[str, ...]:
    """The SuperAnimal keypoint names in the model's own output order -- read, not guessed."""
    from deeplabcut.pose_estimation_pytorch.config.pose import PoseConfig

    cfg = PoseConfig.build_for_superanimal_inference(
        super_animal="superanimal_quadruped", model_name=model_name, detector_name=None,
        max_individuals=1, device="cpu",
    )
    return tuple(cfg["metadata"]["bodyparts"])


# -- the EfficientDet anchor table ----------------------------------------------------------------------


def fit_effdet_anchors(
    frames_and_refs: Iterable[tuple[np.ndarray, BBox]], table_path: Path, min_score: float = 0.4
) -> Path:
    """Solve the EfficientDet anchor table out of the model's own box predictions on dog frames.

    For a prediction that is right, the size equation `h = exp(dh) * anchor_h` gives the anchor height outright, and
    the median over many frames is robust; `centre_error` below then checks the decode order. Written next to the
    artifact so the bake-off -- and any Kotlin port -- reads one table instead of guessing constants.
    """
    path = table_path.parent / "det_effdet_lite0_320_int8.tflite"
    rt = Tflite(path)
    levels = EffDetLite.LEVELS
    probe = EffDetLite(rt, {f"{s}_{k}": (s, s) for s, _ in levels for k in range(9)})
    cx, cy, keys = probe.anchor_grid()
    samples: dict[tuple[int, int], list[tuple[float, float, float]]] = {}
    for frame, ref in frames_and_refs:
        rgb = cv2.cvtColor(cv2.resize(frame, (320, 320), interpolation=cv2.INTER_LINEAR), cv2.COLOR_BGR2RGB)
        outs = rt(np.ascontiguousarray(rgb[None], dtype=np.uint8))
        cls = np.asarray(next(o for o in outs if o.shape[-1] > 4)[0])
        box = np.asarray(next(o for o in outs if o.shape[-1] == 4)[0])
        w_px = (ref[2] - ref[0]) * 320.0 / frame.shape[1]
        h_px = (ref[3] - ref[1]) * 320.0 / frame.shape[0]
        rcx, rcy = (ref[0] + ref[2]) / 2 * 320.0 / frame.shape[1], (ref[1] + ref[3]) / 2 * 320.0 / frame.shape[0]
        for i in np.argsort(cls[:, EffDetLite.dog])[::-1][:8]:
            if cls[i, EffDetLite.dog] < min_score:
                continue
            ah = h_px / math.exp(float(np.clip(box[i, 2], -8, 8)))
            aw = w_px / math.exp(float(np.clip(box[i, 3], -8, 8)))
            stride, k = keys[int(i)]
            if not (0.1 * stride < ah < 20 * stride and 0.1 * stride < aw < 20 * stride):
                continue
            # how far the decoded centre would sit from the reference centre, as a fraction of the box
            ex = (cx[i] + float(box[i, 1]) * aw - rcx) / max(w_px, 1.0)
            ey = (cy[i] + float(box[i, 0]) * ah - rcy) / max(h_px, 1.0)
            samples.setdefault((stride, k), []).append((ah, aw, math.hypot(ex, ey)))
    if not samples:
        raise RuntimeError("no EfficientDet anchor could be solved from these frames")
    table, report = {}, []
    for key, vals in sorted(samples.items()):
        arr = np.array(vals, np.float32)
        ah, aw = float(np.median(arr[:, 0])), float(np.median(arr[:, 1]))
        table[f"{key[0]}_{key[1]}"] = [round(ah, 3), round(aw, 3)]
        report.append((key, len(vals), ah, aw, float(np.median(arr[:, 2]))))
    table_path.write_text(json.dumps(table, indent=2, sort_keys=True) + "\n")
    for key, n, ah, aw, err in report:
        log.info("stride %3d anchor %d: n=%4d  h=%7.2f (%.2f x stride)  w=%7.2f (%.2f x)  centre error %.3f of the box",
                 key[0], key[1], n, ah, ah / key[0], aw, aw / key[0], err)
    return table_path


# -- CLI -----------------------------------------------------------------------------------------------


def clip_names(manifest: Mapping[str, Any], wanted: str) -> list[str]:
    names = [c["name"] for c in manifest["clips"]]
    if wanted == "all":
        return names
    picked = {n.strip() for n in wanted.split(",")}
    unknown = picked - set(names)
    if unknown:
        raise SystemExit(f"unknown clip(s): {', '.join(sorted(unknown))}; known: {', '.join(names)}")
    return [n for n in names if n in picked]


def main(
    argv: Sequence[str] | None = None,
    *,
    builders: Mapping[str, Callable[[], Runner]] | None = None,
    reference: Any = None,
    config: str = str(ROOT / "config.yaml"),
    clips_dir: Path = FALLBACK / "clips",
    manifest_path: Path = MANIFEST,
) -> int:
    import yaml

    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--export-dir", default=str(DEFAULT_EXPORT_DIR), help="where Task 0.1 left the artifacts")
    p.add_argument("--out", default=str(DEFAULT_OUT), help="CSV to write")
    p.add_argument("--clips", default="all", help="'all' or a comma-separated list of clip names")
    p.add_argument("--models", default="all", help="'all' or a comma-separated subset of candidate names")
    p.add_argument("--config", default=config)
    p.add_argument("--clips-dir", default=str(clips_dir))
    p.add_argument("--manifest", default=str(manifest_path))
    p.add_argument("--limit", type=int, default=None, help="stop each clip after N frames (quick pass)")
    p.add_argument("--no-audio", action="store_true", help="skip the YAMNet reference (no TF Hub fetch)")
    p.add_argument("--fit-effdet-anchors", action="store_true", help="derive the EfficientDet anchor table and stop")
    args = p.parse_args(argv)
    logging.basicConfig(level="INFO", format="%(message)s")

    export_dir = Path(args.export_dir)
    report_path = export_dir / "export_report.json"
    if not report_path.is_file():
        raise SystemExit(f"{report_path} not found: run scripts/export_android_models.py first")
    report = json.loads(report_path.read_text())
    cfg = yaml.safe_load(Path(args.config).read_text())
    manifest = json.loads(Path(args.manifest).read_text())
    clips = clip_names(manifest, args.clips)

    candidates = candidates_from_report(report, export_dir, cfg, builders=builders)
    if args.models != "all":
        wanted = {n.strip() for n in args.models.split(",")}
        candidates = [c for c in candidates if c.name in wanted]

    clip_cache = {
        name: read_clip(Path(args.clips_dir) / f"{name}.mp4", cfg["data"]["fps"], limit=args.limit)
        for name in clips
    }
    for name, clip in clip_cache.items():
        log.info("%s: %d frames, %d audio windows", name, len(clip.frames), len(clip.windows))

    if args.fit_effdet_anchors:
        server = reference if reference is not None else ServerStack(cfg, with_audio=False)
        refs = {name: reference_passes(clip_cache[name], server) for name in clips[:2]}
        pairs = [(f, r.box) for name in clips[:2] for f, r in zip(clip_cache[name].frames, refs[name]) if r.box]
        log.info("fitting the anchor table on %d reference boxes", len(pairs))
        fit_effdet_anchors(pairs, export_dir / EffDetLite.TABLE_NAME)
        return 0

    server = reference if reference is not None else ServerStack(cfg, with_audio=not args.no_audio)
    cache_path = export_dir / "bakeoff_reference_cache.pkl"
    cached = None
    if cache_path.is_file() and not getattr(args, "fresh_reference", False):
        try:
            import pickle
            with open(cache_path, "rb") as f:
                cached = pickle.load(f)
            if not all(c in cached.get("refs_by_clip", {}) for c in clips):
                cached = None
        except Exception:
            cached = None

    if cached is not None:
        log.info("loading reference passes from cache: %s", cache_path)
        refs_by_clip = cached["refs_by_clip"]
        ms_by_clip = cached["ms_by_clip"]
    else:
        refs_by_clip = {}
        ms_by_clip = {}
        for name in clips:
            server.ms.clear()  # per clip, so a reference row times its own frames and not the whole run's
            refs_by_clip[name] = reference_passes(clip_cache[name], server)
            ms_by_clip[name] = list(server.ms)
        try:
            import pickle
            with open(cache_path, "wb") as f:
                pickle.dump({"refs_by_clip": refs_by_clip, "ms_by_clip": ms_by_clip}, f)
            log.info("saved reference passes to cache: %s", cache_path)
        except Exception as exc:
            log.warning("could not save reference cache: %s", exc)

    ref_ms = [ms for name in clips for ms in ms_by_clip[name]]
    log.info("reference: %d clips, %.0f ms/frame for detect+pose+face", len(clips),
             sum(ref_ms) / max(len(ref_ms), 1))

    rows: list[dict[str, Any]] = [reference_row(name, clip, refs_by_clip[name], ms_by_clip[name])
                                 for name, clip in clip_cache.items()]

    for cand in candidates:
        entry = report.get(cand.name, {})
        export_ok = bool(entry.get("export_ok"))
        path = entry.get("path")
        status, notes, runner = "ok", entry.get("notes", ""), None
        if not export_ok or not path or not Path(path).is_file():
            status = "not exported (Task 0.1)"
        else:
            try:
                runner = cand.factory()
            except Exception as exc:  # noqa: BLE001  (loading is part of the measurement: record it and carry on)
                status = f"load failed: {type(exc).__name__}: {exc}"[:300]
        size_mb = round(Path(path).stat().st_size / 1e6, 2) if path else None
        for name, clip in clip_cache.items():
            rt = getattr(runner, "rt", None) if status == "ok" else None
            if rt is not None:
                rt.reset_timing()
            row = bakeoff_clip(cand, runner if status == "ok" else None, clip, refs_by_clip[name], server,
                               status=status, notes=notes, artifact_mb=size_mb, export_ok=export_ok)
            if status != "ok":
                row["notes"] = f"{notes}; no measurement: {status}".strip("; ")
            rows.append(row)
        log.info("%-34s %s", cand.name, status)

    out = write_csv(rows, Path(args.out))
    print(f"\nwrote {out} ({len(rows)} rows)")
    summarise(rows)
    return 0


def reference_row(name: str, clip: ClipFrames, refs: Sequence[RefFrame], ms: Sequence[float]) -> dict[str, Any]:
    """The reference's own row: every metric is 1.0 by construction, which is the definition of the baseline."""
    det_found = sum(1 for r in refs if r.box is not None)
    row: dict[str, Any] = {
        "model": "server_reference", "clip": name, "frames": len(clip.frames),
        "modality": "det+pose+face+audio",
        "license": "server stack: ultralytics AGPL-3.0 / DLC SuperAnimal non-commercial / DogFLW CC BY-NC 4.0 / YAMNet Apache-2.0",
        "artifact_mb": "", "export_ok": "", "status": "reference",
        "notes": "what the server computes today; every other row is scored against it",
        "det_recall_vs_ref": 1.0, "mean_iou_vs_ref": 1.0, "kp_pck@0.1_vs_ref": 1.0,
        "n_mapped": len(CANONICAL_NAMES), "unmapped_canonical_kps": "",
        "tail_tip_visible_rate": _round(sum(1 for r in refs if r.kps.get("tail_tip") is not None) / det_found)
        if det_found else "",
        "face_ok_rate": _round(sum(1 for r in refs if r.face_ok) / det_found) if det_found else "",
        "ms_mean_cpu": round(sum(ms) / len(ms), 2) if ms else "",
    }
    for kp in CANONICAL_NAMES:
        row[f"kp_pck@0.1_vs_ref.{kp}"] = 1.0 if any(r.kps.get(kp) for r in refs) else ""
    return row


if __name__ == "__main__":
    raise SystemExit(main())
