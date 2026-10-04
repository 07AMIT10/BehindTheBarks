#!/usr/bin/env python
"""Task 1.5: Export golden fixtures for Android Kotlin unit and parity testing.

Produces three tiers of fixtures:
1. Logic fixtures: per frame, keypoints + landmarks + bbox + wag + audio -> expected features and rules.
   Allows Kotlin FeatureExtractor and RulesEngine to be verified bit-for-bit without ML models.
2. Model I/O fixtures: 8 input tensors per model (.bin + shape JSON) + expected outputs for checking
   Kotlin LiteRT/OpenCL execution and pre/post-processing parity.
3. Audio fixtures: 5s PCM16 audio file + expected YAMNet window scores + expected debounced AudioEvents.
"""

from __future__ import annotations

import argparse
import json
import logging
from pathlib import Path
from typing import Any, Sequence

import numpy as np

ROOT = Path(__file__).resolve().parent.parent

from backend.contracts import AudioEvent, Features, RulesLabel
from backend.vision.mobile_runners import (
    load_mobile_spec,
    load_runtime,
)

log = logging.getLogger(__name__)

DEFAULT_OUT = ROOT / "tests" / "android" / "fixtures"
CLIPS_MOBILE = ROOT / "data" / "fallback" / "events_mobile"


def export_logic_fixtures(out_dir: Path, clips: Sequence[str] | None = None, limit_frames: int = 60) -> list[Path]:
    """Export 60-frame logic fixtures per fallback clip."""
    out_dir.mkdir(parents=True, exist_ok=True)
    available = sorted(CLIPS_MOBILE.glob("*.jsonl"))
    if not available:
        raise FileNotFoundError(f"no mobile events found in {CLIPS_MOBILE}; run precompute_events.py --profile mobile first")

    exported = []
    for events_path in available:
        stem = events_path.stem
        if clips is not None and stem not in clips:
            continue

        records = []
        treats: list[float] = []
        audio_events: list[dict] = []
        with events_path.open() as f:
            all_lines = [json.loads(line) for line in f]

        for rec in all_lines:
            if rec["type"] == "treat":
                treats.append(float(rec["data"]["ts"]))
            elif rec["type"] == "audio":
                audio_events.append(rec["data"])

        for rec in all_lines:
            if rec["type"] == "frame":
                if len(records) >= limit_frames:
                    break
                d = rec["data"]
                bbox = d.get("bbox")
                track = bbox  # single-dog tracking
                pose_in = bbox  # crop box
                face_in = [bbox[0], bbox[1], (bbox[0] + bbox[2]) / 2, (bbox[1] + bbox[3]) / 2] if bbox else None
                ts = d["ts"]
                treat_recent = any(t <= ts <= t + 10.0 for t in treats)
                recent_audio = [a for a in audio_events if ts - 5.0 <= a["ts"] <= ts + 0.25]
                row = {
                    "frame_idx": len(records),
                    "ts": ts,
                    "det": bbox + [d.get("bbox_conf", 0.5)] if bbox else None,
                    "track": track,
                    "pose_in": pose_in,
                    "keypoints": d.get("body_keypoints", {}),
                    "face_in": face_in,
                    "face": d.get("face_landmarks"),
                    "wag_roi": d.get("features", {}).get("tail_wag_hz"),
                    "audio_pcm_ref": None,
                    "audio": recent_audio,
                    "treat": treat_recent,
                    "features": d.get("features", {}),
                    "rules": {
                        "ts": ts,
                        "emotion": "unknown",
                        "confidence": 0.0,
                        "scores": {e: 0.0 for e in ("happy", "excited", "relaxed", "anxious", "fearful", "aggressive", "disinterested", "unknown")}
                    },
                }
                records.append(row)
            elif rec["type"] == "rules" and records:
                records[-1]["rules"] = rec["data"]

        out_path = out_dir / f"{stem}.jsonl"
        with out_path.open("w") as f:
            for row in records:
                f.write(json.dumps(row) + "\n")
        exported.append(out_path)
        log.info("Exported logic fixture: %s (%d frames, %d bytes)", out_path, len(records), out_path.stat().st_size)

    return exported


def export_model_io_fixtures(out_dir: Path, n_samples: int = 8) -> dict[str, Path]:
    """Export 8 model I/O tensor fixtures per model (.bin + shape JSON)."""
    out_dir.mkdir(parents=True, exist_ok=True)
    models_dir = ROOT / "out" / "android_models"
    spec = load_mobile_spec()
    exported = {}

    rng = np.random.default_rng(seed=42)

    # 1. Detector: [1, 3, 320, 320] float32 -> [1, 84, 2100] float32
    det_path = models_dir / "det_yolo26n_320_int8.tflite"
    if det_path.is_file():
        det_dir = out_dir / "detector"
        det_dir.mkdir(parents=True, exist_ok=True)
        rt = load_runtime(det_path)
        for i in range(n_samples):
            inp = rng.uniform(0.0, 1.0, size=(1, 3, 320, 320)).astype(np.float32) if i > 0 else np.zeros((1, 3, 320, 320), np.float32)
            outs = rt(inp)
            out = outs[0]

            (det_dir / f"input_{i}.bin").write_bytes(inp.tobytes())
            (det_dir / f"input_{i}_shape.json").write_text(json.dumps({"shape": list(inp.shape), "dtype": "float32"}))
            (det_dir / f"output_{i}.bin").write_bytes(out.tobytes())
            (det_dir / f"output_{i}_shape.json").write_text(json.dumps({"shape": list(out.shape), "dtype": "float32"}))
        exported["detector"] = det_dir

    # 2. Pose: [1, 3, 256, 256] float32 -> output_0 [1, 17, 512], output_1 [1, 17, 512]
    pose_path = models_dir / "pose_rtmpose_ap10k_litert.tflite"
    if pose_path.is_file():
        pose_dir = out_dir / "pose"
        pose_dir.mkdir(parents=True, exist_ok=True)
        rt = load_runtime(pose_path)
        for i in range(n_samples):
            inp = rng.normal(0.0, 1.0, size=(1, 3, 256, 256)).astype(np.float32) if i > 0 else np.zeros((1, 3, 256, 256), np.float32)
            outs = rt(inp)
            out_x, out_y = outs[0], outs[1]

            (pose_dir / f"input_{i}.bin").write_bytes(inp.tobytes())
            (pose_dir / f"input_{i}_shape.json").write_text(json.dumps({"shape": list(inp.shape), "dtype": "float32"}))
            # Concatenate or save out_x
            (pose_dir / f"output_{i}.bin").write_bytes(out_x.tobytes())
            (pose_dir / f"output_{i}_shape.json").write_text(json.dumps({
                "shape": list(out_x.shape),
                "dtype": "float32",
                "simcc_y_shape": list(out_y.shape)
            }))
        exported["pose"] = pose_dir

    # 3. Face: [1, 384, 384, 3] float32 -> [1, 92] float32
    face_path = models_dir / "face_dog_landmarks_384.tflite"
    if face_path.is_file():
        face_dir = out_dir / "face"
        face_dir.mkdir(parents=True, exist_ok=True)
        rt = load_runtime(face_path)
        for i in range(n_samples):
            inp = rng.uniform(0.0, 1.0, size=(1, 384, 384, 3)).astype(np.float32) if i > 0 else np.zeros((1, 384, 384, 3), np.float32)
            outs = rt(inp)
            out = outs[0]

            (face_dir / f"input_{i}.bin").write_bytes(inp.tobytes())
            (face_dir / f"input_{i}_shape.json").write_text(json.dumps({"shape": list(inp.shape), "dtype": "float32"}))
            (face_dir / f"output_{i}.bin").write_bytes(out.tobytes())
            (face_dir / f"output_{i}_shape.json").write_text(json.dumps({"shape": list(out.shape), "dtype": "float32"}))
        exported["face"] = face_dir

    # 4. Audio: [15600] float32 -> [1, 521] float32
    audio_path = models_dir / "audio_yamnet.tflite"
    if audio_path.is_file():
        audio_dir = out_dir / "audio"
        audio_dir.mkdir(parents=True, exist_ok=True)
        rt = load_runtime(audio_path)
        for i in range(n_samples):
            inp = rng.uniform(-0.5, 0.5, size=(15600,)).astype(np.float32) if i > 0 else np.zeros((15600,), np.float32)
            outs = rt(inp)
            out = outs[0]

            (audio_dir / f"input_{i}.bin").write_bytes(inp.tobytes())
            (audio_dir / f"input_{i}_shape.json").write_text(json.dumps({"shape": list(inp.shape), "dtype": "float32"}))
            (audio_dir / f"output_{i}.bin").write_bytes(out.tobytes())
            (audio_dir / f"output_{i}_shape.json").write_text(json.dumps({"shape": list(out.shape), "dtype": "float32"}))
        exported["audio"] = audio_dir

    return exported


def export_audio_fixtures(out_dir: Path, duration_s: float = 5.0) -> Path:
    """Export 5s PCM16 audio fixture + expected YAMNet scores per window + debounced events."""
    out_dir.mkdir(parents=True, exist_ok=True)
    sr = 16000
    n_samples = int(duration_s * sr)

    # Create deterministic audio stream: 1s silence, 1.5s 400Hz bark tone burst, 2.5s silence
    t = np.linspace(0, duration_s, n_samples, endpoint=False)
    sig = np.zeros(n_samples, dtype=np.float32)
    # Burst between 1.0s and 2.5s
    burst_idx = (t >= 1.0) & (t <= 2.5)
    sig[burst_idx] = 0.5 * np.sin(2 * np.pi * 350 * t[burst_idx])

    pcm16 = (sig * 32767).astype(np.int16)
    pcm_path = out_dir / "audio_5s_16k.pcm"
    pcm_path.write_bytes(pcm16.tobytes())

    # Run through AudioEventDetector
    cfg = {
        "data": {
            "audio": {
                "backend": "tflite",
                "model": str(ROOT / "out" / "android_models" / "audio_yamnet.tflite"),
                "hop_s": 0.48,
                "threshold": 0.3,
                "silence_rms": 0.005,
                "debounce_s": 1.0,
            }
        }
    }
    from backend.audio.yamnet_events import AudioEventDetector, WINDOW

    det = AudioEventDetector(cfg)

    # Process windows and record raw window scores
    hop = det.hop
    windows = []
    w_idx = 0
    while w_idx * hop + WINDOW <= len(sig):
        start = w_idx * hop
        window = sig[start : start + WINDOW]
        ts = (start + WINDOW) / sr
        scores = det._model(window)[0].reshape(-1).tolist()
        label, score = det._classify(window)
        windows.append({"idx": w_idx, "ts": ts, "label": label, "score": score, "scores": scores})
        w_idx += 1

    (out_dir / "audio_windows.json").write_text(json.dumps(windows, indent=2))

    # Push full stream and record debounced events
    det.reset()
    events = [ev.model_dump() for ev in det.push(0.0, sig)]
    (out_dir / "audio_events.json").write_text(json.dumps(events, indent=2))

    return out_dir


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--out-dir", type=Path, default=DEFAULT_OUT)
    ap.add_argument("--limit-frames", type=int, default=60)
    args = ap.parse_args(argv)

    out = args.out_dir
    log_dir = out / "logic"
    models_dir = out / "models"
    audio_dir = out / "audio"

    print(f"Exporting logic fixtures to {log_dir}...")
    export_logic_fixtures(log_dir, limit_frames=args.limit_frames)

    print(f"Exporting model I/O fixtures to {models_dir}...")
    export_model_io_fixtures(models_dir)

    print(f"Exporting audio fixtures to {audio_dir}...")
    export_audio_fixtures(audio_dir)
    print("Done! Golden fixtures successfully exported.")


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    main()
