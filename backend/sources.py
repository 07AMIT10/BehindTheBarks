"""Input abstraction: browser | webcam | stream | file video and mic | file audio, chosen by config.

`MediaSources` exposes two pull iterators that share one clock:

    src = MediaSources.from_config(cfg)          # cfg is the parsed config.yaml
    for ts, frame in src.video(): ...            # BGR uint8 frame, downsampled to data.fps
    for ts, chunk in src.audio(): ...            # mono float32, 16 kHz

Timestamps are epoch seconds (like FrameEvent.ts). Live sources stamp the capture time. File
sources stamp `t0 + media_time`, where t0 is the wall time of the first video()/audio() call, so video and audio
line up in both real-time and --fast playback. A chunk is stamped with the time of its first sample.

The `browser` type is push-based: the phone's frames and PCM arrive through push_frame() and
push_audio() (Person B's /ingest socket -> Pipeline.ingest_*), and the same video()/audio() iterators
pull them out. Timestamps are the caller's server-receipt times, so they share the clock above.

Nothing is buffered unboundedly: live video keeps only the newest frame, the mic queue is bounded
(oldest chunk dropped), and real-time file playback drops frames if the consumer falls behind.
"""

from __future__ import annotations

import argparse
import logging
import math
import queue
import subprocess
import threading
import time
from collections import deque
from pathlib import Path
from typing import Any, Callable, Iterator

import cv2
import numpy as np
from scipy.signal import resample_poly

log = logging.getLogger(__name__)

AUDIO_SR = 16_000
Frame = np.ndarray
VideoItem = tuple[float, Frame]
AudioItem = tuple[float, np.ndarray]

BROWSER_STALL_S = 2.0  # no frame for longer than this -> state "stalled"
BROWSER_AUDIO_BUFFER_S = 5.0  # push_audio ring buffer; oldest chunks are dropped beyond this
BROWSER_AUDIO_GAP_S = 0.3  # a chunk starting this far after the previous one ended re-anchors the clock
MAX_JPEG_BYTES = 8_000_000
_SOI = b"\xff\xd8"  # every JPEG starts with this


class _LatestFrameReader(threading.Thread):
    """Reads a live capture as fast as it delivers, keeping only the newest frame.

    `open_capture` returns a cv2.VideoCapture-like object (read/release/isOpened). With
    `reconnect` (streams) a failed read reopens the capture after a short pause.
    """

    def __init__(self, open_capture: Callable[[], Any], halt: threading.Event, reconnect: bool):
        super().__init__(name="video-reader", daemon=True)
        self._open = open_capture
        self._halt = halt
        self._reconnect = reconnect
        self.cond = threading.Condition()
        self.latest: VideoItem | None = None
        self.seq = 0
        self.ended = False
        self.error: Exception | None = None

    def run(self) -> None:
        cap = None
        try:
            while not self._halt.is_set():
                if cap is None:
                    cap = self._open()
                    if not cap.isOpened():
                        cap.release()
                        cap = None
                        if not self._reconnect:
                            raise RuntimeError("could not open video source")
                        self._halt.wait(1.0)
                        continue
                ok, frame = cap.read()
                ts = time.time()
                if not ok:
                    if not self._reconnect:
                        break
                    log.warning("video read failed, reconnecting")
                    cap.release()
                    cap = None
                    self._halt.wait(1.0)
                    continue
                with self.cond:
                    self.latest = (ts, frame)
                    self.seq += 1
                    self.cond.notify_all()
        except Exception as exc:  # surfaced to the consumer, not lost in the thread
            self.error = exc
        finally:
            if cap is not None:
                cap.release()
            with self.cond:
                self.ended = True
                self.cond.notify_all()


class _LogLimiter:
    """Log a given warning at most once per `interval_s`, counting the ones it swallowed."""

    def __init__(self, interval_s: float = 5.0):
        self.interval_s = interval_s
        self._lock = threading.Lock()
        self._last: dict[str, float] = {}
        self._suppressed: dict[str, int] = {}

    def warn(self, key: str, msg: str, *args: Any) -> None:
        now = time.monotonic()
        with self._lock:
            last = self._last.get(key)
            if last is not None and now - last < self.interval_s:
                self._suppressed[key] = self._suppressed.get(key, 0) + 1
                return
            self._last[key] = now
            skipped = self._suppressed.pop(key, 0)
        log.warning(msg + (f" (+{skipped} similar suppressed)" if skipped else ""), *args)


class _BrowserInput:
    """Where the phone's data lands. The push side is O(1) and never blocks or raises.

    Video keeps only the newest JPEG (still encoded: decoding happens in the consumer thread).
    Audio is a ring buffer of PCM chunks bounded to ~BROWSER_AUDIO_BUFFER_S, dropping the oldest.
    """

    def __init__(self, stall_s: float):
        self.stall_s = stall_s
        self.created = time.monotonic()
        self.closed = False
        self.limit = _LogLimiter()
        self.frame_cond = threading.Condition()
        self.frame: tuple[float, bytes] | None = None
        self.frame_seq = 0
        self.last_frame_at: float | None = None
        self.audio_cond = threading.Condition()
        self.audio: deque[tuple[float, int, bytes]] = deque()
        self.audio_s = 0.0
        self.last_audio_at: float | None = None

    def close(self) -> None:
        self.closed = True
        for cond in (self.frame_cond, self.audio_cond):
            with cond:
                cond.notify_all()

    def push_frame(self, jpeg: Any, ts: float) -> None:
        try:
            if self.closed:
                return
            if not isinstance(jpeg, (bytes, bytearray, memoryview)):
                self.limit.warn("frame-type", "browser frame dropped: expected bytes, got %s", type(jpeg).__name__)
                return
            data = jpeg if isinstance(jpeg, bytes) else bytes(jpeg)
            if not (len(data) > 4 and data.startswith(_SOI) and len(data) <= MAX_JPEG_BYTES):
                self.limit.warn("frame-bad", "browser frame dropped: not a JPEG (%d bytes)", len(data))
                return
            with self.frame_cond:
                self.frame = (float(ts), data)
                self.frame_seq += 1
                self.last_frame_at = time.monotonic()
                self.frame_cond.notify_all()
        except Exception as exc:  # the caller is a network handler; nothing may escape
            self.limit.warn("frame-exc", "browser frame dropped: %r", exc)

    def push_audio(self, pcm16: Any, sample_rate: int, ts: float) -> None:
        try:
            if self.closed:
                return
            if not isinstance(pcm16, (bytes, bytearray, memoryview)):
                self.limit.warn("audio-type", "browser audio dropped: expected bytes, got %s", type(pcm16).__name__)
                return
            data = pcm16 if isinstance(pcm16, bytes) else bytes(pcm16)
            sr = int(sample_rate)
            if len(data) == 0 or len(data) % 2 or not 8_000 <= sr <= 192_000:
                self.limit.warn("audio-bad", "browser audio dropped: %d bytes at %d Hz", len(data), sr)
                return
            with self.audio_cond:
                self.audio.append((float(ts), sr, data))
                self.audio_s += len(data) / 2 / sr
                dropped = 0
                while self.audio_s > BROWSER_AUDIO_BUFFER_S and len(self.audio) > 1:
                    _, old_sr, old = self.audio.popleft()
                    self.audio_s -= len(old) / 2 / old_sr
                    dropped += 1
                self.last_audio_at = time.monotonic()
                self.audio_cond.notify_all()
            if dropped:
                self.limit.warn("audio-backlog", "browser audio consumer is behind: dropped %d old chunk(s)", dropped)
        except Exception as exc:
            self.limit.warn("audio-exc", "browser audio dropped: %r", exc)

    def pop_audio(self, timeout: float) -> tuple[float, int, bytes] | None:
        with self.audio_cond:
            self.audio_cond.wait_for(lambda: self.audio or self.closed, timeout=timeout)
            if not self.audio:
                return None
            rec = self.audio.popleft()
            self.audio_s = max(0.0, self.audio_s - len(rec[2]) / 2 / rec[1])
            return rec

    def frame_age_s(self) -> float | None:
        at = self.last_frame_at
        return None if at is None else time.monotonic() - at

    def audio_age_s(self) -> float | None:
        at = self.last_audio_at
        return None if at is None else time.monotonic() - at

    def state(self) -> str:
        if self.closed:
            return "stopped"
        age = self.frame_age_s()
        if age is None:  # nothing yet: stalled once the grace period is up
            age = time.monotonic() - self.created
        return "stalled" if age > self.stall_s else "running"


class _StreamResampler:
    """Streaming any-rate -> 16 kHz mono resampler that emits fixed-size blocks with timestamps.

    Each block goes through resample_poly with `ctx` samples (~10 ms) of the real neighbouring signal on
    both sides, so block edges carry no filter transient; the price is ~10 ms of lookahead. Block starts
    stay a multiple of the decimation factor apart, so the polyphase phase never drifts and the output
    is one continuous 16 kHz signal. `t0` is the timestamp of the first input sample.
    """

    def __init__(self, sr: int, chunk_s: float, t0: float):
        self.sr, self.t0 = sr, t0
        if sr == AUDIO_SR:
            self.up = self.down = 1
            self.block, self.ctx = max(1, round(chunk_s * sr)), 0
        else:
            g = math.gcd(AUDIO_SR, sr)
            self.up, self.down = AUDIO_SR // g, sr // g
            self.block = max(1, round(chunk_s * sr / self.down)) * self.down
            self.ctx = self.down * math.ceil(0.01 * sr / self.down)
        self.buf = np.zeros(self.ctx, np.float32)  # buf[0] is input sample (start - ctx)
        self.start = 0  # input index where the next block begins
        self.total_in = 0

    @property
    def next_start_ts(self) -> float:
        """Where a chunk that continues this stream should begin."""
        return self.t0 + self.total_in / self.sr

    def feed(self, x: np.ndarray) -> list[AudioItem]:
        self.buf = np.concatenate([self.buf, x])
        self.total_in += len(x)
        out: list[AudioItem] = []
        while len(self.buf) >= self.block + 2 * self.ctx:
            seg = self.buf[: self.block + 2 * self.ctx]
            if self.sr == AUDIO_SR:
                y = seg
            else:
                lead = self.ctx * self.up // self.down
                y = resample_poly(seg, self.up, self.down)[lead : lead + self.block * self.up // self.down]
            out.append((self.t0 + self.start / self.sr, np.ascontiguousarray(y, dtype=np.float32)))
            self.start += self.block
            self.buf = self.buf[self.block :]
        return out


class MediaSources:
    def __init__(
        self,
        type: str = "file",
        path: str | None = None,
        device: int = 0,
        audio: str | None = None,
        loop: bool = False,
        fps: float = 8,
        fast: bool = False,
        chunk_s: float = 0.25,
        stall_s: float = BROWSER_STALL_S,
    ):
        if type not in ("browser", "webcam", "stream", "file"):
            raise ValueError(f"unknown source type {type!r}")
        if type in ("stream", "file") and not path:
            raise ValueError(f"source type {type!r} needs a path/URL")
        self.type, self.path, self.device, self.audio_arg = type, path, device, audio
        self.fps, self.chunk_s = float(fps), chunk_s
        self.fast = fast and type == "file"
        if fast and type != "file":
            log.warning("--fast only applies to file sources; ignoring")
        self.loop = loop and type == "file"
        if loop and self.fast:
            log.warning("loop is ignored in fast mode so batch runs terminate")
            self.loop = False
        self._stop = threading.Event()
        self._t0: float | None = None
        self._t0_lock = threading.Lock()
        self._reader: _LatestFrameReader | None = None
        self._loop_period: float | None = None  # seconds; shared so looped A/V stay aligned
        self._in = _BrowserInput(stall_s) if type == "browser" else None
        self._warned_push = False
        if type == "browser" and audio not in (None, "mic"):
            log.warning("data.source.audio is ignored for the browser source (the phone's mic is used)")

    @classmethod
    def from_config(cls, cfg: dict, **overrides: Any) -> "MediaSources":
        """Build from the parsed config.yaml (reads `data.source` and `data.fps`)."""
        data = cfg["data"]
        args = {**data["source"], "fps": data.get("fps", 8)}
        args.update({k: v for k, v in overrides.items() if v is not None})
        return cls(**args)

    # -- lifecycle ---------------------------------------------------------------------------

    def close(self) -> None:
        """Stop iterators and threads and release devices. Safe to call more than once."""
        self._stop.set()
        if self._in is not None:
            self._in.close()
        if self._reader is not None:
            self._reader.join(timeout=2.0)

    def __enter__(self) -> "MediaSources":
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    def start_clock(self) -> float:
        """Wall time of media time 0 (file sources stamp `t0 + media_time`). Starts the clock if needed."""
        return self._start_clock()

    def _start_clock(self) -> float:
        with self._t0_lock:
            if self._t0 is None:
                self._t0 = time.time()
            return self._t0

    def _wait_until(self, wall: float) -> bool:
        """Sleep until wall time; False if shutdown was requested meanwhile."""
        delay = wall - time.time()
        if delay > 0:
            return not self._stop.wait(delay)
        return not self._stop.is_set()

    # -- browser source: push side and health -------------------------------------------------

    def push_frame(self, jpeg: bytes, ts: float) -> None:
        """Browser source: hand over one JPEG. O(1), non-blocking, never raises; only the newest is kept."""
        if self._in is None:
            self._warn_not_browser()
            return
        self._in.push_frame(jpeg, ts)

    def push_audio(self, pcm16: bytes, sample_rate: int, ts: float) -> None:
        """Browser source: hand over mono Int16-LE PCM. O(1), non-blocking, never raises."""
        if self._in is None:
            self._warn_not_browser()
            return
        self._in.push_audio(pcm16, sample_rate, ts)

    def _warn_not_browser(self) -> None:
        if not self._warned_push:
            self._warned_push = True
            log.warning("push_frame/push_audio ignored: source type is %r, not 'browser'", self.type)

    def state(self) -> str:
        """"running" | "stalled" | "stopped". Only the browser source can stall (no frame for stall_s)."""
        if self._in is not None:
            return self._in.state()
        return "stopped" if self._stop.is_set() else "running"

    def last_frame_age_s(self) -> float | None:
        """Seconds since the last pushed frame arrived (browser source), None if none yet."""
        return None if self._in is None else self._in.frame_age_s()

    def audio_ok(self) -> bool:
        """Browser source: audio chunks are arriving (one within the last stall_s)."""
        if self._in is None:
            return not self._stop.is_set()
        age = self._in.audio_age_s()
        return age is not None and age <= self._in.stall_s and not self._in.closed

    # -- video -------------------------------------------------------------------------------

    def video(self) -> Iterator[VideoItem]:
        """Yield (timestamp, BGR frame) at ~data.fps until the source ends or close() is called."""
        self._start_clock()
        if self.type == "browser":
            return self._browser_video()
        if self.type == "file":
            return self._file_video()
        return self._live_video()

    def _file_video(self) -> Iterator[VideoItem]:
        cap = cv2.VideoCapture(str(self.path))
        if not cap.isOpened():
            raise RuntimeError(f"could not open video file {self.path}")
        try:
            native = cap.get(cv2.CAP_PROP_FPS) or 30.0
            n_frames = cap.get(cv2.CAP_PROP_FRAME_COUNT)
            if n_frames > 0:
                self._loop_period = n_frames / native
            step = 1.0 / self.fps
            t0, offset = self._t0, 0.0
            while True:
                idx, next_emit = 0, 0.0
                while not self._stop.is_set():
                    if not cap.grab():
                        break
                    media_t = idx / native
                    idx += 1
                    if media_t + 1e-9 < next_emit:
                        continue
                    next_emit = max(next_emit + step, media_t - step)
                    ts = t0 + offset + media_t
                    if not self.fast:
                        if not self._wait_until(ts):
                            return
                        if time.time() - ts > step:
                            continue  # consumer fell behind: drop, keep the newest
                    ok, frame = cap.retrieve()
                    if ok:
                        yield ts, frame
                if not self.loop or self._stop.is_set():
                    return
                offset += self._loop_period or idx / native
                cap.set(cv2.CAP_PROP_POS_FRAMES, 0)
        finally:
            cap.release()

    def _browser_video(self) -> Iterator[VideoItem]:
        """Newest pushed frame at ~data.fps; frames pushed faster than that are dropped undecoded."""
        inp = self._in
        step = 1.0 / self.fps
        next_t, last_seq = time.monotonic(), 0
        while not self._stop.is_set():
            if self._stop.wait(max(0.0, next_t - time.monotonic())):
                break
            with inp.frame_cond:
                inp.frame_cond.wait_for(lambda: inp.frame_seq != last_seq or inp.closed, timeout=0.5)
                if inp.frame_seq == last_seq:
                    continue
                (ts, jpeg), last_seq = inp.frame, inp.frame_seq
            frame = cv2.imdecode(np.frombuffer(jpeg, np.uint8), cv2.IMREAD_COLOR)
            if frame is None or frame.ndim != 3:
                inp.limit.warn("frame-decode", "browser frame dropped: JPEG did not decode (%d bytes)", len(jpeg))
                continue
            next_t = max(next_t + step, time.monotonic())  # never burst to catch up
            yield ts, frame

    def _live_video(self) -> Iterator[VideoItem]:
        if self.type == "webcam":
            open_capture = lambda: cv2.VideoCapture(int(self.device))  # noqa: E731
        else:
            open_capture = lambda: cv2.VideoCapture(str(self.path))  # noqa: E731
        reader = self._reader = _LatestFrameReader(
            open_capture, self._stop, reconnect=self.type == "stream"
        )
        reader.start()
        step = 1.0 / self.fps
        next_t, last_seq = time.monotonic(), 0
        while not self._stop.is_set():
            if self._stop.wait(max(0.0, next_t - time.monotonic())):
                break
            with reader.cond:
                reader.cond.wait_for(
                    lambda: reader.seq != last_seq or reader.ended or self._stop.is_set(),
                    timeout=1.0,
                )
                if reader.seq == last_seq:
                    if reader.ended:
                        break
                    continue
                item, last_seq = reader.latest, reader.seq
            next_t = max(next_t + step, time.monotonic())  # never burst to catch up
            yield item
        if reader.error is not None:
            raise reader.error

    # -- audio -------------------------------------------------------------------------------

    def audio(self) -> Iterator[AudioItem]:
        """Yield (timestamp, mono float32 16 kHz chunk) until the source ends or close() is called.

        Audio comes from data.source.audio if set ("mic" or a file path); otherwise from the
        video file's own track (file mode) or the microphone (webcam/stream).
        """
        self._start_clock()
        if self.type == "browser":
            return self._browser_audio()
        arg = self.audio_arg
        if arg == "mic" or (arg is None and self.type != "file"):
            return self._mic_audio()
        return self._file_audio(Path(arg) if arg else Path(str(self.path)), override=arg is not None)

    def _browser_audio(self) -> Iterator[AudioItem]:
        """Pushed PCM as 16 kHz float32 chunks of ~chunk_s, stamped like the mic source.

        push_audio's ts is when a chunk reached the server, i.e. the end of its samples. The first chunk
        after a start or a gap anchors the sample clock at ts - duration; later chunks continue that clock,
        so network jitter doesn't show up as timestamp jitter. A chunk that starts more than
        BROWSER_AUDIO_GAP_S after the previous one ended (phone paused, backlog dropped) re-anchors.
        """
        inp, rs = self._in, None
        while not self._stop.is_set():
            rec = inp.pop_audio(timeout=0.25)
            if rec is None:
                continue
            ts, sr, raw = rec
            x = np.frombuffer(raw, dtype="<i2").astype(np.float32) / 32768.0
            start = ts - len(x) / sr
            if rs is None or rs.sr != sr or start - rs.next_start_ts > BROWSER_AUDIO_GAP_S:
                if rs is not None:
                    log.info("browser audio discontinuity (rate %d -> %d Hz): restarting the resampler", rs.sr, sr)
                rs = _StreamResampler(sr, self.chunk_s, start)
            yield from rs.feed(x)

    def _file_audio(self, path: Path, override: bool) -> Iterator[AudioItem]:
        data = _decode_audio(path, required=override)
        if data is None or len(data) == 0:
            log.warning("no audio available from %s", path)
            return
        if self.loop:
            # Fit the audio to the video length so both streams loop in lockstep.
            if self._loop_period is None:
                cap = cv2.VideoCapture(str(self.path))
                n, fps = cap.get(cv2.CAP_PROP_FRAME_COUNT), cap.get(cv2.CAP_PROP_FPS) or 30.0
                cap.release()
                self._loop_period = n / fps if n > 0 else None
            if self._loop_period:
                want = int(round(self._loop_period * AUDIO_SR))
                data = np.pad(data, (0, max(0, want - len(data))))[:want]
        chunk = max(1, int(self.chunk_s * AUDIO_SR))
        t0, offset = self._t0, 0.0
        while True:
            for i in range(0, len(data), chunk):
                c = data[i : i + chunk]
                ts = t0 + offset + i / AUDIO_SR
                # A live mic delivers a chunk once it is complete, so pace on its end time.
                if not self.fast and not self._wait_until(ts + len(c) / AUDIO_SR):
                    return
                if self._stop.is_set():
                    return
                yield ts, c
            if not self.loop:
                return
            offset += len(data) / AUDIO_SR

    def _mic_audio(self) -> Iterator[AudioItem]:
        import sounddevice as sd

        q: queue.Queue[AudioItem] = queue.Queue(maxsize=64)  # ~16 s; oldest dropped when full

        def callback(indata, frames, time_info, status):
            if status:
                log.warning("mic status: %s", status)
            age = max(0.0, time_info.currentTime - time_info.inputBufferAdcTime)
            item = (time.time() - age, indata[:, 0].copy())
            try:
                q.put_nowait(item)
            except queue.Full:
                try:
                    q.get_nowait()
                except queue.Empty:
                    pass
                q.put_nowait(item)

        blocksize = max(1, int(self.chunk_s * AUDIO_SR))
        with sd.InputStream(
            samplerate=AUDIO_SR, channels=1, dtype="float32", blocksize=blocksize, callback=callback
        ):
            while not self._stop.is_set():
                try:
                    yield q.get(timeout=0.25)
                except queue.Empty:
                    continue


def _decode_audio(path: Path, required: bool) -> np.ndarray | None:
    """Decode any audio/video file to mono float32 16 kHz with ffmpeg."""
    cmd = ["ffmpeg", "-v", "error", "-i", str(path), "-vn", "-ac", "1", "-ar", str(AUDIO_SR),
           "-f", "f32le", "-"]
    try:
        proc = subprocess.run(cmd, capture_output=True)
    except FileNotFoundError:
        raise RuntimeError("ffmpeg not found on PATH (brew install ffmpeg)") from None
    if proc.returncode != 0:
        if required:
            raise RuntimeError(f"ffmpeg could not read audio from {path}: {proc.stderr.decode()[:300]}")
        return None  # e.g. the video has no audio track
    return np.frombuffer(proc.stdout, dtype=np.float32).copy()


# -- CLI -------------------------------------------------------------------------------------


def main(argv: list[str] | None = None) -> None:
    import yaml

    p = argparse.ArgumentParser(description="Preview video frames and audio RMS from a source.")
    p.add_argument("--config", default="config.yaml")
    p.add_argument("--source", choices=["webcam", "stream", "file"])
    p.add_argument("--path", help="video file path or stream URL")
    p.add_argument("--device", type=int)
    p.add_argument("--audio", help='audio override: a file path or "mic"')
    p.add_argument("--fps", type=float)
    p.add_argument("--loop", action="store_true", default=None)
    p.add_argument("--fast", action="store_true", help="file mode: play as fast as possible")
    p.add_argument("--no-display", action="store_true", help="print frame stats instead of a window")
    args = p.parse_args(argv)

    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    cfg = yaml.safe_load(Path(args.config).read_text()) if Path(args.config).exists() else {"data": {"source": {}}}
    src = MediaSources.from_config(
        cfg, type=args.source, path=args.path, device=args.device, audio=args.audio,
        fps=args.fps, loop=args.loop, fast=args.fast,
    )

    def audio_loop() -> None:
        acc, n, last = 0.0, 0, None
        try:
            for ts, chunk in src.audio():
                last = ts if last is None else last
                acc += float(np.sum(chunk.astype(np.float64) ** 2))
                n += len(chunk)
                if ts - last >= 1.0:
                    print(f"audio  t={ts:.2f}  rms={np.sqrt(acc / n):.4f}  ({n} samples)", flush=True)
                    acc, n, last = 0.0, 0, ts
        except Exception as exc:
            log.error("audio source failed: %s", exc)

    audio_thread = threading.Thread(target=audio_loop, name="audio-preview", daemon=True)
    audio_thread.start()
    count, last_report = 0, time.time()
    try:
        for ts, frame in src.video():
            count += 1
            if args.no_display:
                if time.time() - last_report >= 1.0:
                    print(f"video  t={ts:.2f}  {count} frames  shape={frame.shape}", flush=True)
                    count, last_report = 0, time.time()
            else:
                cv2.putText(frame, f"{ts:.2f}", (10, 30), cv2.FONT_HERSHEY_SIMPLEX, 1, (0, 255, 0), 2)
                cv2.imshow("source (q to quit)", frame)
                if cv2.waitKey(1) & 0xFF == ord("q"):
                    break
    except KeyboardInterrupt:
        pass
    finally:
        src.close()
        audio_thread.join(timeout=2.0)
        cv2.destroyAllWindows()


if __name__ == "__main__":
    main()
