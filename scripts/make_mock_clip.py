"""Render MockPipeline into a committed placeholder demo clip (H.264 + events.jsonl + meta.json).

    .venv/bin/python scripts/make_mock_clip.py [--out backend/demo/clips/mock-90s] [--duration 90] [--fps 8]

Needs imageio-ffmpeg (static ffmpeg binary, no system install). The frames are flat synthetic
renderings, so 90 s stays well under 1 MB.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from pathlib import Path

import cv2
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import imageio_ffmpeg  # noqa: E402

from backend.demo.mock_pipeline import MockPipeline  # noqa: E402


def render(frame, w: int, h: int, phase: str) -> np.ndarray:
    img = np.full((h, w, 3), 60, np.uint8)
    sx, sy = w / 640.0, h / 480.0
    if frame.dog_detected and frame.bbox:
        x1, y1, x2, y2 = [int(v * (sx if i % 2 == 0 else sy)) for i, v in enumerate(frame.bbox)]
        cv2.rectangle(img, (x1, y1), (x2, y2), (127, 227, 232), 2)
        for kp in (frame.body_keypoints or {}).values():
            if kp:
                cv2.circle(img, (int(kp[0] * sx), int(kp[1] * sy)), 4, (127, 227, 232), -1)
    cv2.putText(img, f"MOCK {phase}", (12, 30), cv2.FONT_HERSHEY_SIMPLEX, 0.8, (200, 200, 200), 2)
    return img


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="backend/demo/clips/mock-90s")
    ap.add_argument("--duration", type=float, default=90.0)
    ap.add_argument("--fps", type=float, default=8.0)
    ap.add_argument("--width", type=int, default=640)
    ap.add_argument("--height", type=int, default=480)
    a = ap.parse_args(argv)
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)

    clock = [0.0]
    mp = MockPipeline({"data": {"fps": a.fps}}, clock=lambda: clock[0])
    ff = imageio_ffmpeg.get_ffmpeg_exe()
    proc = subprocess.Popen(
        [ff, "-v", "error", "-y", "-f", "rawvideo", "-pix_fmt", "bgr24",
         "-s", f"{a.width}x{a.height}", "-r", str(a.fps), "-i", "-",
         "-c:v", "libx264", "-pix_fmt", "yuv420p", "-movflags", "+faststart", str(out / "clip.mp4")],
        stdin=subprocess.PIPE)
    assert proc.stdin is not None
    n = int(a.duration * a.fps)
    treat_at = 12.0  # relaxed -> excited boundary in the mock script
    with (out / "events.jsonl").open("w") as f:
        for i in range(n):
            t = round(i / a.fps, 3)
            frame, audio, rules = mp.step(t)
            f.write(json.dumps({"t": t, "type": "frame", "data": frame.model_dump(mode="json")}) + "\n")
            for e in audio:
                f.write(json.dumps({"t": t, "type": "audio", "data": e.model_dump(mode="json")}) + "\n")
            f.write(json.dumps({"t": t, "type": "rules", "data": rules.model_dump(mode="json")}) + "\n")
            if abs(t - treat_at) < 0.5 / a.fps:
                f.write(json.dumps({"t": t, "type": "treat", "data": {"ts": t}}) + "\n")
            proc.stdin.write(np.ascontiguousarray(render(frame, a.width, a.height, mp.phase_at(t))).tobytes())
    proc.stdin.close()
    proc.wait()
    if proc.returncode != 0:
        return 1
    (out / "meta.json").write_text(json.dumps({"name": "Mock day", "emotion": "excited", "duration_s": a.duration}))
    size = (out / "clip.mp4").stat().st_size
    print(f"wrote {out}/clip.mp4 ({size} bytes), events.jsonl, meta.json in {time.process_time():.1f}s cpu")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
