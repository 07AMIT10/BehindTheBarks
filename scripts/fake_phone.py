"""Pretend to be the phone: play a video file (+ optional audio) into the browser source in real time.

Person B's /ingest socket will call Pipeline.ingest_frame / ingest_audio; this script calls the same
two push methods directly, so the whole browser path can be tested without a phone or Person B.

    python scripts/fake_phone.py data/fallback/raw/german_shepherd_treat.webm
    python scripts/fake_phone.py clip.mp4 --audio bark.wav --portrait --loop --duration 30
    python scripts/fake_phone.py clip.mp4 --vision --events out/phone_events.jsonl

Frames are JPEG-encoded at ~640 px on the long side (quality 70) at --fps; audio is mono Int16-LE at
48 kHz in ~100 ms chunks, like the /ingest protocol. `ts` is time.time() at each push, i.e. what the
server would stamp on receipt. With --portrait each frame is rotated 90 degrees first.
"""

from __future__ import annotations

import argparse
import logging
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Any

import cv2
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

log = logging.getLogger("fake_phone")


def decode_pcm16(path: str, sample_rate: int) -> np.ndarray | None:
    """Any audio/video file -> mono int16 at `sample_rate` (ffmpeg), or None if there is no audio."""
    cmd = ["ffmpeg", "-v", "error", "-i", path, "-vn", "-ac", "1", "-ar", str(sample_rate), "-f", "s16le", "-"]
    try:
        proc = subprocess.run(cmd, capture_output=True)
    except FileNotFoundError:
        raise RuntimeError("ffmpeg not found on PATH (brew install ffmpeg)") from None
    if proc.returncode != 0 or not proc.stdout:
        return None
    return np.frombuffer(proc.stdout, dtype="<i2").copy()


def encode_frame(frame: np.ndarray, portrait: bool, long_side: int = 640, quality: int = 70) -> bytes:
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


class FakePhone:
    """Feeds `sink.push_frame(jpeg, ts)` and `sink.push_audio(pcm16, sample_rate, ts)` from files."""

    def __init__(
        self,
        sink: Any,
        video: str,
        audio: str | None = None,
        fps: float = 10.0,
        portrait: bool = False,
        loop: bool = False,
        sample_rate: int = 48_000,
        chunk_ms: int = 100,
        duration: float | None = None,
    ):
        self.sink, self.video, self.audio_path = sink, video, audio
        self.fps, self.portrait, self.loop = fps, portrait, loop
        self.sample_rate, self.chunk_ms, self.duration = sample_rate, chunk_ms, duration
        self._halt = threading.Event()
        self._threads: list[threading.Thread] = []
        self.frames_sent = 0
        self.chunks_sent = 0

    def start(self) -> "FakePhone":
        self._t0 = time.monotonic()
        for target in (self._video_loop, self._audio_loop):
            t = threading.Thread(target=target, name=f"fake-phone-{target.__name__}", daemon=True)
            t.start()
            self._threads.append(t)
        return self

    def stop(self) -> None:
        self._halt.set()
        for t in self._threads:
            t.join(timeout=2.0)

    def wait(self) -> None:
        """Block until both feeders have finished (file ended without --loop, or --duration hit)."""
        for t in self._threads:
            while t.is_alive() and not self._halt.is_set():
                t.join(timeout=0.2)

    def _due(self, media_t: float) -> bool:
        """Sleep until `media_t` seconds after start. False when told to stop or the duration is up."""
        if self.duration is not None and media_t > self.duration:
            return False
        return not self._halt.wait(max(0.0, self._t0 + media_t - time.monotonic()))

    def _video_loop(self) -> None:
        cap = cv2.VideoCapture(self.video)
        if not cap.isOpened():
            log.error("could not open %s", self.video)
            return
        try:
            native = cap.get(cv2.CAP_PROP_FPS) or 30.0
            period = cap.get(cv2.CAP_PROP_FRAME_COUNT) / native
            step, offset = 1.0 / self.fps, 0.0
            while True:
                idx, next_emit = 0, 0.0
                while True:
                    if not cap.grab():
                        break
                    media_t = idx / native
                    idx += 1
                    if media_t + 1e-9 < next_emit:
                        continue
                    next_emit = max(next_emit + step, media_t - step)
                    if not self._due(offset + media_t):
                        return
                    ok, frame = cap.retrieve()
                    if ok:
                        self.sink.push_frame(encode_frame(frame, self.portrait), time.time())
                        self.frames_sent += 1
                if not self.loop:
                    return
                offset += period
                cap.set(cv2.CAP_PROP_POS_FRAMES, 0)
        finally:
            cap.release()

    def _audio_loop(self) -> None:
        pcm = decode_pcm16(self.audio_path or self.video, self.sample_rate)
        if pcm is None:
            log.warning("no audio in %s; sending video only", self.audio_path or self.video)
            return
        n = self.sample_rate * self.chunk_ms // 1000
        offset = 0.0
        while True:
            for i in range(0, len(pcm), n):
                chunk = pcm[i : i + n]
                # A phone sends a chunk once it has been recorded, so pace on the chunk's end time.
                if not self._due(offset + (i + len(chunk)) / self.sample_rate):
                    return
                self.sink.push_audio(chunk.astype("<i2").tobytes(), self.sample_rate, time.time())
                self.chunks_sent += 1
            if not self.loop:
                return
            offset += len(pcm) / self.sample_rate


def main(argv: list[str] | None = None) -> None:
    import yaml

    from backend.sources import MediaSources

    ap = argparse.ArgumentParser(description="Simulate the phone camera/mic against the browser source.")
    ap.add_argument("video", help="video file to play as the phone's camera")
    ap.add_argument("--audio", help="audio file for the phone's mic (default: the video's own track)")
    ap.add_argument("--config", default="config.yaml")
    ap.add_argument("--fps", type=float, default=10.0, help="frames the phone sends per second")
    ap.add_argument("--portrait", action="store_true", help="rotate frames 90 degrees (phone held upright)")
    ap.add_argument("--loop", action="store_true")
    ap.add_argument("--duration", type=float, help="stop after this many seconds")
    ap.add_argument("--sample-rate", type=int, default=48_000)
    ap.add_argument("--vision", action="store_true", help="also run detect+pose+face+features on what arrives")
    ap.add_argument("--events", help="with --vision: write FrameEvents as JSON lines to this path")
    args = ap.parse_args(argv)

    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    cfg = yaml.safe_load(Path(args.config).read_text()) if Path(args.config).exists() else {"data": {"fps": 8}}
    src = MediaSources(type="browser", fps=cfg["data"].get("fps", 8))
    phone = FakePhone(
        src, args.video, args.audio, fps=args.fps, portrait=args.portrait, loop=args.loop,
        sample_rate=args.sample_rate, duration=args.duration,
    )

    stats = {"chunks": 0, "samples": 0, "sq": 0.0, "first": None, "last_end": None}

    def audio_loop() -> None:
        for ts, chunk in src.audio():
            stats["chunks"] += 1
            stats["samples"] += len(chunk)
            stats["sq"] += float(np.sum(chunk.astype(np.float64) ** 2))
            stats["first"] = ts if stats["first"] is None else stats["first"]
            stats["last_end"] = ts + len(chunk) / 16_000

    threading.Thread(target=audio_loop, name="audio-preview", daemon=True).start()

    pipeline = None
    if args.vision:
        from backend.vision.detect import DogDetector
        from backend.vision.face import FaceLandmarker
        from backend.vision.features import FeatureExtractor
        from backend.vision.pose import PoseEstimator

        pipeline = (DogDetector(cfg), PoseEstimator(cfg), FaceLandmarker(cfg), FeatureExtractor(cfg))
    events_fh = open(args.events, "w") if args.events and pipeline else None

    phone.start()
    n_frames = n_dog = 0
    last_report = time.time()
    try:
        watcher = threading.Thread(target=lambda: (phone.wait(), time.sleep(1.0), src.close()), daemon=True)
        watcher.start()
        for ts, frame in src.video():
            n_frames += 1
            if pipeline:
                det, pose, face, fx = pipeline
                d = det.detect(frame)
                kps = pose.estimate(frame, d.bbox) if d is not None else {}
                lms = face.estimate(frame, kps) if kps else None
                ev = fx.update(ts, d, kps, lms)
                n_dog += ev.dog_detected
                if events_fh:
                    events_fh.write(ev.to_json() + "\n")
            if time.time() - last_report >= 1.0:
                rms = (stats["sq"] / stats["samples"]) ** 0.5 if stats["samples"] else 0.0
                extra = f"  dog in {n_dog}/{n_frames}" if pipeline else ""
                print(
                    f"state={src.state():8s} frame {frame.shape[1]}x{frame.shape[0]} "
                    f"age={time.time() - ts:.2f}s  audio chunks={stats['chunks']} rms={rms:.4f}{extra}",
                    flush=True,
                )
                last_report = time.time()
    except KeyboardInterrupt:
        pass
    finally:
        phone.stop()
        src.close()
        if events_fh:
            events_fh.close()
    print(f"sent {phone.frames_sent} frames, {phone.chunks_sent} audio chunks; consumed {n_frames} frames, "
          f"{stats['chunks']} audio chunks ({stats['samples'] / 16_000:.1f} s at 16 kHz)")


if __name__ == "__main__":
    main()
