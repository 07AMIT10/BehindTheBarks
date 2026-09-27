"""Pretend to be the /camera page: stream frames + a sine tone to the backend's /ingest WebSocket.

Tests the whole phone -> backend -> dashboard path without a phone (Person A's scripts/fake_phone.py
skips the network and calls the pipeline directly; this one goes over the real socket).

    python scripts/ingest_client.py                                   # synthetic frames, 8 fps, 440 Hz tone
    python scripts/ingest_client.py --video clip.mp4 --portrait --duration 60
    python scripts/ingest_client.py --url wss://abc.trycloudflare.com/ingest
    python scripts/ingest_client.py --no-camera                       # mic-only phone (hello camera:false)

Frames: JPEG, ~640 px on the long side, quality 70, byte 0x01 first. Audio: mono Int16 LE at
--sample-rate in 100 ms chunks, byte 0x02 first. Exit code 2 if the server closes the socket.
"""

from __future__ import annotations

import argparse
import json
import math
import sys
import time
from typing import Iterator

import cv2
import numpy as np
from websockets.exceptions import ConnectionClosed
from websockets.sync.client import connect

KIND_FRAME = b"\x01"
KIND_AUDIO = b"\x02"


def hello_message(width: int, height: int, fps: float, sample_rate: int, device: str,
                  facing: str = "back", camera: bool = True) -> str:
    return json.dumps({"type": "hello", "width": width, "height": height, "fps": fps,
                       "sample_rate": sample_rate, "device": device, "facing": facing, "camera": camera})


def frame_message(jpeg: bytes) -> bytes:
    return KIND_FRAME + jpeg


def audio_message(pcm16: bytes) -> bytes:
    return KIND_AUDIO + pcm16


def sine_chunk(sample_rate: int, start_sample: int, n: int, freq: float = 440.0, amp: float = 0.2) -> bytes:
    """n samples of a sine tone starting at sample index start_sample, as Int16 little-endian bytes."""
    t = (np.arange(n) + start_sample) / sample_rate
    return (np.sin(2 * math.pi * freq * t) * amp * 32767).astype("<i2").tobytes()


def synthetic_frame(i: int, width: int = 640, height: int = 360) -> np.ndarray:
    """A grey frame with a moving circle and the frame number, so motion is visible on the dashboard."""
    img = np.full((height, width, 3), 70, np.uint8)
    x = int(width / 2 + width / 3 * math.sin(i / 10))
    cv2.circle(img, (x, height // 2), max(10, min(width, height) // 8), (60, 180, 230), -1)
    cv2.putText(img, f"ingest_client #{i}", (12, 30), cv2.FONT_HERSHEY_SIMPLEX, 0.7, (235, 235, 235), 2)
    return img


def encode_jpeg(frame: np.ndarray, portrait: bool = False, long_side: int = 640, quality: int = 70) -> bytes:
    if portrait:
        frame = cv2.rotate(frame, cv2.ROTATE_90_CLOCKWISE)
    h, w = frame.shape[:2]
    if max(h, w) > long_side:
        s = long_side / max(h, w)
        frame = cv2.resize(frame, (round(w * s), round(h * s)), interpolation=cv2.INTER_AREA)
    ok, buf = cv2.imencode(".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, quality])
    if not ok:
        raise RuntimeError("JPEG encoding failed")
    return buf.tobytes()


def frames(video: str | None, portrait: bool) -> Iterator[bytes]:
    """Endless JPEG frames: from a video file (looped) or synthetic."""
    i = 0
    if video is None:
        while True:
            yield encode_jpeg(synthetic_frame(i), portrait)
            i += 1
    cap = cv2.VideoCapture(video)
    if not cap.isOpened():
        raise SystemExit(f"cannot open video {video!r}")
    while True:
        ok, img = cap.read()
        if not ok:
            cap.set(cv2.CAP_PROP_POS_FRAMES, 0)
            ok, img = cap.read()
            if not ok:
                raise SystemExit(f"no frames in {video!r}")
        yield encode_jpeg(img, portrait)


def run(url: str, video: str | None = None, fps: float = 8.0, portrait: bool = False, duration: float = 0.0,
        sample_rate: int = 48_000, audio: bool = True, camera: bool = True, device: str = "ingest_client",
        quiet: bool = False) -> dict:
    """Stream until `duration` seconds pass (0 = forever). Returns counters. Raises ConnectionClosed."""
    gen = frames(video, portrait) if camera else None
    first = next(gen) if gen else None
    width, height = (0, 0)
    if first is not None:
        img = cv2.imdecode(np.frombuffer(first, np.uint8), cv2.IMREAD_COLOR)
        height, width = img.shape[:2]
    chunk = sample_rate // 10  # 100 ms
    sent = {"frames": 0, "audio": 0, "bytes": 0}
    with connect(url, max_size=None, open_timeout=10) as ws:
        ws.send(hello_message(width, height, fps if camera else 0, sample_rate, device, camera=camera))
        t0 = time.monotonic()
        next_frame = next_audio = next_log = t0
        sample = 0
        while duration <= 0 or time.monotonic() - t0 < duration:
            now = time.monotonic()
            if gen is not None and now >= next_frame:
                jpeg = first if first is not None else next(gen)
                first = None
                ws.send(frame_message(jpeg))
                sent["frames"] += 1
                sent["bytes"] += len(jpeg) + 1
                next_frame += 1.0 / fps
            if audio and now >= next_audio:
                pcm = sine_chunk(sample_rate, sample, chunk)
                ws.send(audio_message(pcm))
                sample += chunk
                sent["audio"] += 1
                sent["bytes"] += len(pcm) + 1
                next_audio += 0.1
            if not quiet and now >= next_log:
                print(f"[{now - t0:6.1f}s] frames={sent['frames']} audio_chunks={sent['audio']} "
                      f"kB={sent['bytes'] // 1024}", flush=True)
                next_log += 5.0
            waits = [t for t, on in ((next_frame, gen is not None), (next_audio, audio)) if on]
            time.sleep(max(0.0, min(waits, default=now + 0.1) - time.monotonic()))
    return sent


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--url", default="ws://localhost:8000/ingest")
    ap.add_argument("--video", default=None, help="video file to loop (default: synthetic frames)")
    ap.add_argument("--fps", type=float, default=8.0)
    ap.add_argument("--portrait", action="store_true", help="rotate frames 90 degrees (phone held upright)")
    ap.add_argument("--duration", type=float, default=0.0, help="seconds; 0 = until Ctrl-C")
    ap.add_argument("--sample-rate", type=int, default=48_000)
    ap.add_argument("--no-audio", action="store_true")
    ap.add_argument("--no-camera", action="store_true", help="mic-only phone: hello camera:false, no frames")
    ap.add_argument("--device", default="ingest_client")
    a = ap.parse_args()
    try:
        sent = run(a.url, a.video, a.fps, a.portrait, a.duration, a.sample_rate, not a.no_audio,
                   not a.no_camera, a.device)
    except ConnectionClosed as exc:
        rcvd = exc.rcvd
        print(f"server closed the socket: code={rcvd.code if rcvd else None} "
              f"reason={rcvd.reason if rcvd else ''!r}", file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        return 0
    print(f"done: {sent}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
