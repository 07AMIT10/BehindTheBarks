"""FrameEvent feature extraction over a rolling keypoint window.

    fx = FeatureExtractor(cfg, source="live")        # reads `data.features`
    event = fx.update(ts, det, kps, lms)             # det: Detection | None, kps: canonical keypoints,
                                                     # lms: face landmarks | None -> FrameEvent

Call `update` once per processed frame, in time order, with the outputs of DogDetector.detect(),
PoseEstimator.estimate() and FaceLandmarker.estimate(). The extractor keeps a rolling window
(`window_s`, ~3 s) of confident keypoints and a longer history (`baseline_s`) of body height.

Rules that hold for every feature:
  * A feature is None when its inputs are missing or below the keypoint confidence threshold. Nothing
    is filled in from a guess. One exception is stated where it happens: tail_wag_hz is 0.0, not None,
    when the tail is visible but not moving periodically.
  * Distances are divided by the dog's body length (median withers-hip distance over the window), so
    camera distance does not matter. Without a withers-hip pair the bbox size stands in for it.
  * Geometry assumes a roughly side-on, upright camera: "up" is the image's up direction. A dog
    seen from directly above or head-on gives None for tail_height instead of a wrong number.
  * Scalars get light EMA smoothing (`smooth`). Everything tunable is in config.yaml `data.features`.

Camera fps caps what tail_wag_hz can see: at the default 8 fps the Nyquist limit is 4 Hz, so faster
wags alias. The measurement band is clipped just below Nyquist.
"""

from __future__ import annotations

import argparse
import math
import time
from collections import Counter, deque
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Sequence

import numpy as np

from backend.contracts import FrameEvent, Features
from backend.vision import face as F
from backend.vision.detect import BBox, Detection
from backend.vision.keypoint_map import Keypoint
from backend.vision.wag import estimate_wag_hz

DEFAULTS: dict[str, Any] = {
    "window_s": 3.0,
    "gap_reset_s": 2.0,  # no dog for this long empties the window (the standing baseline survives)
    "bbox_scale_factor": 0.8,  # body length as a fraction of the bbox's longer side, when withers/hip are missing
    # wag_source: pose | roi | both
    "wag_source": "pose",
    "wag": {"source": "pose"},
    # tail_height: tail-tip offset perpendicular to the back line, in body lengths
    "tail_height_scale": 0.4,  # offset (body lengths) that maps to +-1
    "tail_min_upright": 0.3,  # |vertical component| of the back line's perpendicular; below = None
    # tail_wag_hz
    "wag_min_hz": 0.5,
    "wag_max_hz": 6.0,
    "wag_min_samples": 10,
    "wag_min_span_s": 1.5,
    "wag_max_gap_s": 0.75,  # the latest unbroken run of tail samples is used; gaps longer than this end it
    "wag_min_std": 0.03,  # tail oscillation std (body lengths) below which the tail counts as still
    "wag_min_peak_ratio": 0.3,  # share of spectral power at the peak below which the motion is not periodic
    # ear_position
    "ear_up_min": 0.6,  # ear vector's upward component (cos of angle from vertical) for "up"
    "ear_back_cos": 0.85,  # cos of angle between ear vector and the backward head axis for "back"
    "ear_back_max_pitch": 0.7,  # "back" only claimed when |head axis vertical component| is below this
    "ear_min_len": 0.03,  # ear vector and head axis must be at least this many body lengths
    "ear_vote_s": 1.0,
    # mouth_open: lip gap / face size, mapped [closed, full] -> [0, 1]
    "mouth_closed": 0.03,
    "mouth_full": 0.25,
    # body_lowering
    "baseline_s": 30.0,
    "baseline_pct": 90.0,
    "baseline_min_s": 2.0,
    "lowering_deadband": 0.05,  # fractional drop in height ignored as noise
    "lowering_full": 0.4,  # fractional drop in height that maps to 1.0
    "height_s": 1.0,  # current height = median over this long
    # motion_energy: median keypoint speed in body lengths per second, mapped [floor, full] -> [0, 1]
    "motion_keypoints": ["nose", "withers", "hip", "left_front_paw", "right_front_paw", "left_back_paw", "right_back_paw"],
    "motion_floor": 0.2,
    "motion_full": 1.0,
    "motion_max_dt_s": 1.0,  # frame pairs further apart than this are skipped
    "smooth": {"tail_height": 0.4, "mouth_open": 0.5, "body_lowering": 0.4, "motion_energy": 0.5},
}

# (body keypoint base, body keypoint tip, face landmark base indices, face landmark tip index)
_EAR_SIDES = (
    ("left_ear_base", "left_ear_tip", (F.EAR_UPPER_BASE_LEFT, F.EAR_LOWER_BASE_LEFT), F.EAR_TIP_LEFT),
    ("right_ear_base", "right_ear_tip", (F.EAR_UPPER_BASE_RIGHT, F.EAR_LOWER_BASE_RIGHT), F.EAR_TIP_RIGHT),
)
_EYES_AND_NOSE = tuple(range(16, 36))  # landmark span that does not change when the mouth opens


@dataclass
class _Sample:
    ts: float
    pts: dict[str, np.ndarray]  # confident keypoints only, full-frame pixels
    bbox: BBox | None


def _alpha_to_tau(alpha: float, fps: float = 8.0) -> float:
    """Convert legacy per-frame alpha at given fps to continuous time constant tau in seconds."""
    if alpha >= 1.0:
        return 0.0
    if alpha <= 0.0:
        return float("inf")
    return -(1.0 / fps) / math.log(1.0 - alpha)


@dataclass
class _Ema:
    tau_s: float
    value: float | None = None
    last_ts: float | None = None

    def step(self, x: float | None, ts: float, reset_gap: float) -> float | None:
        """Blend a new value in; None in -> None out. A long gap restarts the average."""
        if x is None:
            return None
        if self.value is None or self.last_ts is None or ts - self.last_ts > reset_gap:
            self.value = x
        else:
            dt = max(ts - self.last_ts, 0.0)
            if self.tau_s > 0.0 and dt > 0.0:
                alpha = 1.0 - math.exp(-dt / self.tau_s)
            else:
                alpha = 1.0
            self.value = alpha * x + (1.0 - alpha) * self.value
        self.last_ts = ts
        return self.value


@dataclass
class _State:
    window: deque[_Sample] = field(default_factory=deque)
    heights: deque[tuple[float, float]] = field(default_factory=deque)  # (ts, body height in body lengths)
    ear_votes: deque[tuple[float, str]] = field(default_factory=deque)
    last_dog_ts: float | None = None


class FeatureExtractor:
    def __init__(self, cfg: dict, source: str = "live"):
        d = cfg["data"]
        self.p: dict[str, Any] = {**DEFAULTS, **d.get("features", {})}
        raw_smooth = {**DEFAULTS["smooth"], **d.get("features", {}).get("smooth", {})}
        self.p["smooth"] = raw_smooth
        if "smooth_tau_s" in d.get("features", {}):
            self.tau_s = {k: float(v) for k, v in d["features"]["smooth_tau_s"].items()}
        else:
            self.tau_s = {k: _alpha_to_tau(float(a)) for k, a in raw_smooth.items()}
        self.conf_thr: float = d.get("keypoint_conf_threshold", 0.3)
        self.source = source
        self._smooth = {k: _Ema(tau) for k, tau in self.tau_s.items()}
        wag_cfg = d.get("features", {}).get("wag", {})
        self.wag_source = wag_cfg.get("source", d.get("features", {}).get("wag_source", self.p.get("wag_source", "pose")))
        self.last_wag: dict[str, float | None] = {"pose": None, "roi": None}
        self._s = _State()

    def reset(self) -> None:
        """Forget all history (new clip, or the source restarted)."""
        self._s = _State()
        for e in self._smooth.values():
            e.value = e.last_ts = None

    # -- public ------------------------------------------------------------------------------

    def update(
        self,
        ts: float,
        det: Detection | None,
        kps: dict[str, Keypoint | None] | None,
        lms: Sequence[Sequence[float]] | None,
        roi_wag_hz: float | None = None,
    ) -> FrameEvent:
        """Add one frame and return its FrameEvent. Frames must arrive in time order."""
        s = self._s
        if det is None:
            if s.last_dog_ts is not None and ts - s.last_dog_ts > self.p["gap_reset_s"]:
                s.window.clear()
                s.ear_votes.clear()
                for e in self._smooth.values():
                    e.value = e.last_ts = None
            self._trim(ts)
            # No dog: not in the zone. Every other feature is None.
            return FrameEvent(ts=ts, source=self.source, dog_detected=False, features=Features(in_feeding_zone=False))

        s.last_dog_ts = ts
        kps = kps or {}
        pts = {n: np.array(k[:2], float) for n, k in kps.items() if k is not None and k[2] >= self.conf_thr}
        s.window.append(_Sample(ts, pts, det.bbox))
        self._trim(ts)
        scale = self._body_scale()

        raw_height = self._tail_height(pts, scale)
        pose_wag = self._tail_wag_hz(scale)
        if self.wag_source == "pose":
            wag_hz = pose_wag
        elif self.wag_source == "roi":
            wag_hz = roi_wag_hz
        elif self.wag_source == "both":
            wag_hz = roi_wag_hz if roi_wag_hz is not None else pose_wag
        else:
            wag_hz = pose_wag
        self.last_wag = {"pose": pose_wag, "roi": roi_wag_hz}

        features = Features(
            tail_height=self._smoothed("tail_height", raw_height, ts, -1.0, 1.0),
            tail_wag_hz=wag_hz,
            ear_position=self._ear_position(ts, pts, lms, scale),
            mouth_open=self._smoothed("mouth_open", self._mouth_open(lms), ts, 0.0, 1.0),
            body_lowering=self._smoothed("body_lowering", self._body_lowering(ts, pts, det.bbox, scale), ts, 0.0, 1.0),
            motion_energy=self._smoothed("motion_energy", self._motion_energy(scale), ts, 0.0, 1.0),
            in_feeding_zone=det.in_feeding_zone,
        )
        return FrameEvent(
            ts=ts,
            source=self.source,
            dog_detected=True,
            bbox=det.bbox,
            bbox_conf=min(max(det.conf, 0.0), 1.0),
            body_keypoints={n: (tuple(float(v) for v in k) if k is not None and k[2] >= self.conf_thr else None) for n, k in kps.items()},
            face_landmarks=[(float(x), float(y)) for x, y in lms] if lms is not None else None,
            features=features,
        )

    # -- window bookkeeping ------------------------------------------------------------------

    def _trim(self, ts: float) -> None:
        s = self._s
        while s.window and ts - s.window[0].ts > self.p["window_s"]:
            s.window.popleft()
        while s.heights and ts - s.heights[0][0] > self.p["baseline_s"]:
            s.heights.popleft()
        while s.ear_votes and ts - s.ear_votes[0][0] > self.p["ear_vote_s"]:
            s.ear_votes.popleft()

    def _smoothed(self, name: str, x: float | None, ts: float, lo: float, hi: float) -> float | None:
        y = self._smooth[name].step(x, ts, self.p["window_s"])
        return None if y is None else float(min(max(y, lo), hi))

    def _body_scale(self) -> float | None:
        """Body length in pixels: median withers-hip distance in the window, else bbox-based."""
        lens = [np.linalg.norm(w.pts["withers"] - w.pts["hip"]) for w in self._s.window if "withers" in w.pts and "hip" in w.pts]
        lens = [x for x in lens if x > 1.0]
        if lens:
            return float(np.median(lens))
        sides = [max(w.bbox[2] - w.bbox[0], w.bbox[3] - w.bbox[1]) for w in self._s.window if w.bbox is not None]
        return float(np.median(sides)) * self.p["bbox_scale_factor"] if sides else None

    # -- tail --------------------------------------------------------------------------------

    def _tail_height(self, pts: dict[str, np.ndarray], scale: float | None) -> float | None:
        """Tail tip's offset from the tail base, perpendicular to the back line (withers -> hip), up positive.

        In body lengths, scaled so `tail_height_scale` body lengths above the back line is +1 and the
        same distance below (a tucked tail) is -1.
        """
        if scale is None or not all(n in pts for n in ("withers", "tail_base", "tail_tip")):
            return None
        end = pts.get("hip", pts["tail_base"])
        back = end - pts["withers"]
        norm = np.linalg.norm(back)
        if norm < 1e-6:
            return None
        back /= norm
        up = np.array([back[1], -back[0]])
        if up[1] > 0:
            up = -up  # of the two perpendiculars, the one pointing to the top of the image
        if abs(up[1]) < self.p["tail_min_upright"]:
            return None
        h = float((pts["tail_tip"] - pts["tail_base"]) @ up) / (scale * self.p["tail_height_scale"])
        return min(max(h, -1.0), 1.0)

    def _tail_wag_hz(self, scale: float | None) -> float | None:
        """Dominant frequency of the tail tip's oscillation about the tail base, over the latest unbroken run.

        The tip's position relative to the base is projected on its own principal axis (so it works
        from the side, where a wag looks like fore-aft motion, or from behind), resampled to an even
        grid, detrended, Hann-windowed and read off an FFT with parabolic peak interpolation.
        Returns None if the run is too short or too sparse; 0.0 if the tail is there but still, or
        moves with no clear period.
        """
        p = self.p
        rows = [(w.ts, w.pts["tail_tip"] - w.pts["tail_base"]) for w in self._s.window if "tail_tip" in w.pts and "tail_base" in w.pts]
        if scale is None or len(rows) < p["wag_min_samples"]:
            return None
        run = [rows[-1]]
        for r in reversed(rows[:-1]):
            if run[-1][0] - r[0] > p["wag_max_gap_s"]:
                break
            run.append(r)
        run.reverse()
        if len(run) < p["wag_min_samples"] or run[-1][0] - run[0][0] < p["wag_min_span_s"]:
            return None

        t = np.array([r[0] for r in run])
        xy = np.array([r[1] for r in run]) / scale
        centred = xy - xy.mean(axis=0)
        axis = np.linalg.svd(centred, full_matrices=False)[2][0]
        sig = centred @ axis
        return estimate_wag_hz(
            t,
            sig,
            min_hz=p["wag_min_hz"],
            max_hz=p["wag_max_hz"],
            min_std=p["wag_min_std"],
            min_peak_ratio=p["wag_min_peak_ratio"],
            min_samples=p["wag_min_samples"],
            min_span_s=p["wag_min_span_s"],
            max_gap_s=p["wag_max_gap_s"],
        )

    # -- ears --------------------------------------------------------------------------------

    def _ear_position(
        self, ts: float, pts: dict[str, np.ndarray], lms: Sequence[Sequence[float]] | None, scale: float | None
    ) -> str | None:
        """up | neutral | back | unknown, voted over the last `ear_vote_s`.

        Per ear: the vector from ear base to ear tip, against the head axis (eye midpoint -> nose).
          up      the ear points up in the image (upward cos >= ear_up_min)
          back    the ear lies along the backward head axis (cos >= ear_back_cos), and the head axis is
                  not steep: with the head pointing straight down, "back" and "up" look the same in 2D
          neutral anything else (hanging, half-cocked, pointing forward)
        Face landmarks are used when there are any, otherwise body keypoints. Both ears agreeing gives
        that label, one known ear gives its label, two that disagree give neutral.
        """
        if scale is None:
            return None
        p = self.p
        per_ear: list[str] = []
        for base_n, tip_n, base_idx, tip_idx in _EAR_SIDES:
            if lms is not None:
                L = np.asarray(lms, float)
                nose = L[list(F.NOSE)].mean(axis=0)
                eyes = L[list(F.EYE_LEFT + F.EYE_RIGHT)].mean(axis=0)
                base, tip = L[list(base_idx)].mean(axis=0), L[tip_idx]
            elif all(n in pts for n in ("nose", base_n, tip_n)) and any(e in pts for e in ("left_eye", "right_eye")):
                nose = pts["nose"]
                eyes = np.mean([pts[e] for e in ("left_eye", "right_eye") if e in pts], axis=0)
                base, tip = pts[base_n], pts[tip_n]
            else:
                continue
            fwd, ear = nose - eyes, tip - base
            fl, el = np.linalg.norm(fwd), np.linalg.norm(ear)
            if fl < p["ear_min_len"] * scale or el < p["ear_min_len"] * scale:
                continue
            fwd, ear = fwd / fl, ear / el
            up_cos = -ear[1]
            if up_cos >= p["ear_up_min"]:
                per_ear.append("up")
            elif float(ear @ -fwd) >= p["ear_back_cos"] and abs(fwd[1]) < p["ear_back_max_pitch"]:
                per_ear.append("back")
            else:
                per_ear.append("neutral")
        if per_ear:
            self._s.ear_votes.append((ts, per_ear[0] if len(set(per_ear)) == 1 else "neutral"))
        if not self._s.ear_votes:
            return "unknown"
        return Counter(v for _, v in self._s.ear_votes).most_common(1)[0][0]

    # -- mouth -------------------------------------------------------------------------------

    def _mouth_open(self, lms: Sequence[Sequence[float]] | None) -> float | None:
        """Gap between the upper-lip and lower-lip landmarks over face size, mapped to 0..1.

        Face size is the diagonal of the eye and nose landmarks, which do not move when the mouth opens.
        Needs face landmarks: the body model's jaw points are too unreliable to read a mouth from.
        """
        if lms is None:
            return None
        L = np.asarray(lms, float)
        span = L[list(_EYES_AND_NOSE)]
        size = float(np.linalg.norm(span.max(axis=0) - span.min(axis=0)))
        if size < 1e-6:
            return None
        ratio = float(np.linalg.norm(L[F.LIP_UPPER_MID] - L[F.LIP_LOWER_MID])) / size
        lo, hi = self.p["mouth_closed"], self.p["mouth_full"]
        return min(max((ratio - lo) / (hi - lo), 0.0), 1.0)

    # -- body lowering -----------------------------------------------------------------------

    def _body_lowering(self, ts: float, pts: dict[str, np.ndarray], bbox: BBox | None, scale: float | None) -> float | None:
        """How far the withers/hip have dropped below the dog's own recent standing height.

        Height = (bbox bottom - mean y of withers and hip) in body lengths. The bbox bottom is the
        ground contact, so the number does not depend on where in the frame the dog stands. Paw
        keypoints would be the more direct ground reference but are confident in only a fraction of frames.
        The standing baseline is a high percentile of the height over the last `baseline_s`.
        """
        p, s = self.p, self._s
        ys = [pts[n][1] for n in ("withers", "hip") if n in pts]
        if bbox is not None and ys and scale is not None:
            s.heights.append((ts, (bbox[3] - float(np.mean(ys))) / scale))
        if len(s.heights) < 2 or s.heights[-1][0] - s.heights[0][0] < p["baseline_min_s"]:
            return None
        recent = [h for t, h in s.heights if ts - t <= p["height_s"]]
        if not recent:
            return None
        base = float(np.percentile([h for _, h in s.heights], p["baseline_pct"]))
        if base <= 0.05:
            return None
        drop = (base - float(np.median(recent))) / base
        return min(max((drop - p["lowering_deadband"]) / (p["lowering_full"] - p["lowering_deadband"]), 0.0), 1.0)

    # -- motion ------------------------------------------------------------------------------

    def _motion_energy(self, scale: float | None) -> float | None:
        """Mean over the window of the median keypoint speed (body lengths / s), mapped to 0..1.

        Body points only (nose, withers, hip, paws): a wagging tail or flopping ear is not body motion.
        Keypoints are resampled onto a fixed 5 Hz grid (0.2 s step) before differencing,
        ensuring that jitter/dt does not scale artificially with camera frame rate.
        """
        p = self.p
        if scale is None:
            return None
        w = list(self._s.window)
        if len(w) < 2:
            return None

        t_start = w[0].ts
        t_end = w[-1].ts
        grid_dt = 0.2  # fixed 5 Hz grid
        if t_end - t_start < grid_dt:
            return None

        grid_times = np.arange(t_start, t_end + 1e-6, grid_dt)
        if len(grid_times) < 2:
            return None

        grid_pts = []
        w_idx = 0
        w_len = len(w)
        for gt in grid_times:
            while w_idx < w_len - 2 and w[w_idx + 1].ts < gt:
                w_idx += 1
            a, b = w[w_idx], w[w_idx + 1]
            dt = b.ts - a.ts
            if dt > p["motion_max_dt_s"] or dt <= 0:
                grid_pts.append({})
                continue
            frac = (gt - a.ts) / dt if dt > 0 else 0.0
            frac = min(max(frac, 0.0), 1.0)
            pts = {}
            for n in p["motion_keypoints"]:
                if n in a.pts and n in b.pts:
                    pts[n] = (1.0 - frac) * a.pts[n] + frac * b.pts[n]
            grid_pts.append(pts)

        speeds = []
        for g0, g1 in zip(grid_pts, grid_pts[1:]):
            if not g0 or not g1:
                continue
            common = [n for n in p["motion_keypoints"] if n in g0 and n in g1]
            if len(common) < 2:
                continue
            speeds.append(float(np.median([np.linalg.norm(g1[n] - g0[n]) for n in common])) / grid_dt / scale)
        if len(speeds) < 2:
            return None
        lo, hi = p["motion_floor"], p["motion_full"]
        return min(max((float(np.mean(speeds)) - lo) / (hi - lo), 0.0), 1.0)


# -- CLI: feature summary on a clip ------------------------------------------------------------

def main(argv: list[str] | None = None) -> None:
    import cv2
    import yaml

    from backend.sources import MediaSources
    from backend.vision.detect import DogDetector, draw_debug
    from backend.vision.face import FaceLandmarker, draw_face
    from backend.vision.pose import PoseEstimator, draw_skeleton
    from backend.vision.videoio import DebugVideoWriter

    ap = argparse.ArgumentParser(description="Run the vision stack + feature extraction on a clip; write events and a debug video.")
    ap.add_argument("--config", default="config.yaml")
    ap.add_argument("--path", required=True, help="video file")
    ap.add_argument("--out", default="out/debug_features.mp4")
    ap.add_argument("--events", help="also write FrameEvents as JSON lines to this path")
    ap.add_argument("--fps", type=float)
    args = ap.parse_args(argv)

    cfg = yaml.safe_load(Path(args.config).read_text())
    det, pose, face, fx = DogDetector(cfg), PoseEstimator(cfg), FaceLandmarker(cfg), FeatureExtractor(cfg)
    fps = args.fps or cfg["data"].get("fps", 8)
    Path(args.out).parent.mkdir(parents=True, exist_ok=True)
    writer, events, ms_all = None, [], []
    events_fh = open(args.events, "w") if args.events else None
    with MediaSources(type="file", path=args.path, fps=fps, fast=True) as src:
        for ts, frame in src.video():
            t = time.perf_counter()
            d = det.detect(frame)
            kps = pose.estimate(frame, d.bbox) if d is not None else {}
            lms = face.estimate(frame, kps) if kps else None
            ev = fx.update(ts, d, kps, lms)
            ms_all.append((time.perf_counter() - t) * 1000)
            events.append(ev)
            if events_fh:
                events_fh.write(ev.to_json() + "\n")
            img = draw_face(draw_skeleton(draw_debug(frame, d, det.zone, ms_all[-1]), kps), lms, face.head_box(kps) if kps else None)
            f = ev.features
            lines = [f"{k}={v:.2f}" if isinstance(v, float) else f"{k}={v}" for k, v in f.model_dump().items()]
            for i, line in enumerate(lines):
                cv2.putText(img, line, (10, 40 + 18 * i), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 255, 255), 1, cv2.LINE_AA)
            if writer is None:
                writer = DebugVideoWriter(args.out, fps)
            writer.write(img)
    if writer is not None:
        writer.release()
    if events_fh:
        events_fh.close()

    with_dog = [e for e in events if e.dog_detected]
    print(f"{len(events)} frames, {len(with_dog)} with a dog; detect+pose+face+features mean {np.mean(ms_all):.0f} ms/frame")
    for name in Features.model_fields:
        vals = [getattr(e.features, name) for e in with_dog]
        known = [v for v in vals if v is not None]
        if not known:
            print(f"  {name:15s} non-null 0%")
        elif isinstance(known[0], (bool, str)):
            print(f"  {name:15s} non-null {len(known) / len(vals):4.0%}  {dict(Counter(known))}")
        else:
            a = np.array(known, float)
            print(f"  {name:15s} non-null {len(known) / len(vals):4.0%}  min {a.min():.2f}  median {np.median(a):.2f}  max {a.max():.2f}")
    print(f"wrote {args.out}")


if __name__ == "__main__":
    main()
