"""The handoff interface between Data and Web.

Web constructs a Pipeline, awaits run() with three callbacks, and never imports anything else from
backend/vision/ or backend/audio/:

    pipeline = Pipeline(cfg)                      # cfg is the parsed config.yaml; loads every model
    task = asyncio.create_task(pipeline.run(on_frame_event, on_audio_event, on_rules_label))
    pipeline.mark_treat(time.time())              # "treat dropped" button
    pipeline.ingest_frame(jpeg, time.time())      # browser source only: phone camera
    pipeline.ingest_audio(pcm16, 48_000, time.time())
    pipeline.status(); pipeline.latest_frame_jpeg(); pipeline.stop()

Threading. Inference is blocking native code (torch, TFLite, TensorFlow) that releases the GIL, so
asyncio would only wrap it in executors. Plain threads it is:

    video thread     source frames -> detect -> pose -> face -> features -> rules   (one frame at a time)
    audio thread     source audio  -> YAMNet -> AudioEvent                          (independent of video)
    watchdog thread  live sources only: while the source is stalled, feeds "no dog" frames so the
                     rules fall to `unknown` (see "Disconnects" in CLAUDE.md)
    stats thread     logs per-stage latency every 5 s

Nothing in a worker ever waits on the caller. Workers append events to one bounded outbox and wake
the caller's event loop once; run() drains the outbox there, so callbacks run on that loop's thread.
If the callbacks fall behind, the oldest events are dropped (real-time sources) rather than queued
without bound. Frames are dropped upstream by the source: live and real-time file sources hand over
only the newest frame when inference is slower than the frame rate.

`--fast` file mode (data.source.fast) is the offline batch mode and is deterministic: audio is
analysed first, then video, and audio events are emitted merged into the frame stream by timestamp.
Nothing is dropped and the timings don't depend on machine speed.
"""

from __future__ import annotations

import asyncio
import bisect
import logging
import threading
import time
from collections import defaultdict, deque
from typing import Any, Callable

import cv2
import numpy as np

from backend.contracts import AudioEvent, FrameEvent, RulesLabel

log = logging.getLogger(__name__)

FrameCallback = Callable[[FrameEvent], None]
AudioCallback = Callable[[AudioEvent], None]
RulesCallback = Callable[[RulesLabel], None]
# Debug hook for tooling (scripts/run_pipeline.py): called on the video thread for every processed frame.
FrameTap = Callable[[np.ndarray, FrameEvent, RulesLabel, "list[AudioEvent]"], None]

OUTBOX_MAX = 512  # ~1 min of frame+rules events at 8 fps; older ones are dropped when the caller is that far behind
STATS_EVERY_S = 5.0
WATCHDOG_TICK_S = 0.5
AUDIO_LOOKAHEAD_S = 0.25  # an AudioEvent stamped this far after a frame still counts for it (window ends lag)
AUDIO_HISTORY_S = 30.0
LIVE_TYPES = ("browser", "webcam", "stream")


class _Stats:
    """Per-stage timings collected between two log lines."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._ms: dict[str, list[float]] = defaultdict(list)

    def add(self, stage: str, ms: float) -> None:
        with self._lock:
            self._ms[stage].append(ms)

    def take(self) -> dict[str, list[float]]:
        with self._lock:
            out, self._ms = dict(self._ms), defaultdict(list)
        return out


class Pipeline:
    def __init__(
        self,
        config: dict[str, Any],
        *,
        frame_tap: FrameTap | None = None,
        components: dict[str, Any] | None = None,
    ) -> None:
        """Build the pipeline from the parsed config.yaml (the full dict; reads its `data` section).

        Loading models is done here, not in run(), so a slow start doesn't delay the first event.
        `frame_tap` is a tooling hook; `components` lets tests inject fakes for any of
        source, detector, pose, face, features, rules, audio.
        """
        c = components or {}
        data = config["data"]
        self.cfg = config
        self.frame_tap = frame_tap
        # Imports live here so importing backend.pipeline stays cheap for Web's mock-only runs.
        from backend.fusion.rules import RulesEngine
        from backend.sources import MediaSources
        from backend.vision.features import FeatureExtractor

        self.src = c.get("source") or MediaSources.from_config(config)
        self.fast = bool(self.src.fast)
        self.live = self.src.type in LIVE_TYPES
        self.source_type: str = self.src.type
        self.stall_s = float(data.get("source", {}).get("stall_s", 2.0))
        self.treat_window_s = float(data.get("rules", {}).get("treat_window_s", 10.0))

        if "detector" in c:
            self.detector, self.pose, self.face = c["detector"], c["pose"], c["face"]
        else:
            from backend.vision.detect import DogDetector
            from backend.vision.face import FaceLandmarker
            from backend.vision.pose import PoseEstimator

            self.detector, self.pose, self.face = DogDetector(config), PoseEstimator(config), FaceLandmarker(config)
        self.features = c.get("features") or FeatureExtractor(config, source="live" if self.live else "file")
        self.rules = c.get("rules") or RulesEngine(config)
        if "audio" in c:
            self.audio = c["audio"]
        else:
            from backend.audio.yamnet_events import AudioEventDetector

            self.audio = AudioEventDetector(config)

        self._stop = threading.Event()
        self._started = False
        self._start_mono = time.monotonic()
        self._running = False
        self._threads: list[threading.Thread] = []
        self._workers_alive = 0
        self._workers_done = threading.Event()
        self._audio_done = threading.Event()
        self._lock = threading.Lock()  # guards the small shared state below
        self._step = threading.Lock()  # one thread at a time inside features + rules
        self._error: BaseException | None = None

        self._loop: asyncio.AbstractEventLoop | None = None
        self._wake: asyncio.Event | None = None
        self._wake_pending = False
        self._outbox: deque[tuple[str, Any]] = deque(maxlen=None if self.fast else OUTBOX_MAX)
        self._outbox_dropped = 0

        self._jpeg: bytes | None = None
        self._frame_times: deque[float] = deque(maxlen=64)  # monotonic, per processed frame
        self._last_frame_mono: float | None = None
        self._last_audio_mono: float | None = None
        self._last_ts = -1.0  # newest FrameEvent ts handed to features (they need time order)
        self._audio_ts: list[float] = []  # AudioEvents in time order, for the rules
        self._audio_events: list[AudioEvent] = []
        self._held_audio: deque[AudioEvent] = deque()  # fast mode: audio waiting to be merged into the frame stream
        self._treats: list[float] = []
        self._stats = _Stats()
        self._limit_last: dict[str, float] = {}

    # -- public API --------------------------------------------------------------------------

    async def run(
        self,
        on_frame_event: FrameCallback,
        on_audio_event: AudioCallback,
        on_rules_label: RulesCallback,
    ) -> None:
        """Process the configured source until it ends or stop() is called.

        Callbacks are invoked on the caller's event loop thread and must return quickly.
        - on_frame_event(FrameEvent): ~5-10 per second, one per processed video frame, including
          frames with no dog (dog_detected=False).
        - on_audio_event(AudioEvent): one per YAMNet window, ~every 0.48 s (0.96 s window, 50% hop),
          debounced to onsets and changes (see CLAUDE.md).
        - on_rules_label(RulesLabel): once per processed frame, after hysteresis. Its .scores holds
          a 0..1 score for every emotion in the fixed vocabulary.
        The video loop never waits on the callbacks; frames are dropped rather than queued.

        Raises whatever killed the video thread, if anything did. A callback that raises is logged
        and does not stop the pipeline.
        """
        if self._stop.is_set():
            return
        if self._started:
            raise RuntimeError("Pipeline.run() can only be called once")
        self._started = True
        self._start_mono = time.monotonic()
        self._loop = asyncio.get_running_loop()
        self._wake = asyncio.Event()
        callbacks = {"frame": on_frame_event, "audio": on_audio_event, "rules": on_rules_label}

        workers = [("video", self._video_loop), ("audio", self._audio_loop), ("stats", self._stats_loop)]
        if self.live:
            workers.append(("watchdog", self._watchdog_loop))
        self._workers_alive = sum(1 for n, _ in workers if n in ("video", "audio"))
        self._running = True
        for name, target in workers:
            t = threading.Thread(target=target, name=f"pipeline-{name}", daemon=True)
            self._threads.append(t)
            t.start()
        try:
            while True:
                await self._wake.wait()
                self._wake.clear()
                self._wake_pending = False
                self._drain(callbacks)
                if self._workers_done.is_set() and not self._outbox:
                    break
        finally:
            self.stop()
        if self._error is not None:
            raise self._error

    def latest_frame_jpeg(self) -> bytes | None:
        """Newest raw frame as JPEG (no overlay drawn), or None before the first frame.

        Full resolution, so the pixel coordinates in FrameEvent line up with it directly.
        """
        return self._jpeg

    def mark_treat(self, ts: float) -> None:
        """A treat dropped at `ts`: the rules see treat_event_recent for data.rules.treat_window_s."""
        with self._lock:
            self._treats.append(float(ts))
            del self._treats[:-32]

    def ingest_frame(self, jpeg: bytes, ts: float) -> None:
        """Browser source: one JPEG from the phone, `ts` = server receipt time. Non-blocking, never raises.

        Drops the oldest data when behind. No-op (one warning, ever) unless the source type is browser.
        """
        try:
            self.src.push_frame(jpeg, ts)
        except Exception as exc:
            self._limited("ingest-frame", "ingest_frame failed: %r", exc)

    def ingest_audio(self, pcm16: bytes, sample_rate: int, ts: float) -> None:
        """Browser source: mono Int16-LE PCM from the phone. Same guarantees as ingest_frame."""
        try:
            self.src.push_audio(pcm16, sample_rate, ts)
        except Exception as exc:
            self._limited("ingest-audio", "ingest_audio failed: %r", exc)

    def clock_start(self) -> float:
        """Wall time that file sources count media time from (a frame at t s is stamped clock_start() + t).

        Not part of the Web contract: lets tooling place treats at clip times before a fast batch run.
        """
        return self.src.start_clock()

    def status(self) -> dict[str, Any]:
        """{"source", "state": running|stalled|stopped, "fps", "last_frame_age_s", "audio_ok"}."""
        age = self._frame_age_s()
        return {
            "source": self.source_type,
            "state": self._state(age),
            "fps": round(self._fps(), 2),
            "last_frame_age_s": None if age is None else round(age, 3),
            "audio_ok": self._audio_ok(),
        }

    def stop(self) -> None:
        """Stop all threads and release camera/microphone. Safe to call more than once."""
        self._stop.set()
        self.src.close()
        self._wake_loop()
        me = threading.current_thread()
        deadline = time.monotonic() + 3.0
        for t in self._threads:
            if t is not me and t.is_alive():
                t.join(timeout=max(0.0, deadline - time.monotonic()))
        self._running = False
        self._audio_done.set()

    # -- status helpers ----------------------------------------------------------------------

    def _frame_age_s(self) -> float | None:
        if self.source_type == "browser":
            return self.src.last_frame_age_s()  # measured at receipt, not after inference
        last = self._last_frame_mono
        return None if last is None else time.monotonic() - last

    def _state(self, age: float | None) -> str:
        if not self._running or self._stop.is_set():
            return "stopped"
        if self.live:
            # Never having had a frame counts as stalled once the grace period is over.
            since = age if age is not None else time.monotonic() - self._start_mono
            if since > self.stall_s:
                return "stalled"
        return "running"

    def _fps(self) -> float:
        now = time.monotonic()
        recent = [t for t in list(self._frame_times) if now - t <= 3.0]
        if len(recent) < 2 or now - recent[-1] > 1.5:
            return 0.0
        return (len(recent) - 1) / (recent[-1] - recent[0])

    def _audio_ok(self) -> bool:
        last = self._last_audio_mono
        return self._running and last is not None and time.monotonic() - last <= self.stall_s

    # -- event delivery ----------------------------------------------------------------------

    def _emit(self, kind: str, obj: Any) -> None:
        """Called from worker threads: queue an event and wake the caller's loop. Never blocks."""
        if self._outbox.maxlen is not None and len(self._outbox) == self._outbox.maxlen:
            self._outbox_dropped += 1
        self._outbox.append((kind, obj))
        if not self._wake_pending:
            self._wake_pending = True
            self._wake_loop()

    def _wake_loop(self) -> None:
        loop, wake = self._loop, self._wake
        if loop is None or wake is None:
            return
        try:
            loop.call_soon_threadsafe(wake.set)
        except RuntimeError:  # loop already closed
            pass

    def _drain(self, callbacks: dict[str, Callable[[Any], None]]) -> None:
        for _ in range(len(self._outbox)):
            try:
                kind, obj = self._outbox.popleft()
            except IndexError:
                break
            try:
                callbacks[kind](obj)
            except Exception:
                self._limited(f"callback-{kind}", "on_%s callback raised", kind, exc_info=True)

    def _worker_finished(self) -> None:
        with self._lock:
            self._workers_alive -= 1
            last = self._workers_alive <= 0
        if last:
            self._workers_done.set()
            self._wake_loop()

    # -- video thread ------------------------------------------------------------------------

    def _video_loop(self) -> None:
        try:
            if self.fast:
                while not self._audio_done.wait(0.1):  # batch mode: all audio analysed before the first frame
                    if self._stop.is_set():
                        return
            for ts, frame in self.src.video():
                if self._stop.is_set():
                    break
                try:
                    self._process(ts, frame)
                except Exception:
                    self._limited("frame", "frame at ts=%.2f failed; skipped", ts, exc_info=True)
        except Exception as exc:  # the source itself died (device gone, file unreadable)
            log.exception("video source failed")
            self._error = exc
        finally:
            if self.fast:
                self._flush_held_audio(float("inf"))
            if self.source_type != "file":
                self.src.close()  # a live video source ending takes the mic down with it
            self._worker_finished()

    def _process(self, ts: float, frame: np.ndarray) -> None:
        clock = time.perf_counter
        t0 = clock()
        with self._step:
            if ts < self._last_ts:  # e.g. the watchdog got ahead of a straggler; features need time order
                return
            self._last_ts = ts
            det = self.detector.detect(frame)
            t1 = clock()
            kps = self.pose.estimate(frame, det.bbox) if det is not None else {}
            t2 = clock()
            lms = self.face.estimate(frame, kps) if kps else None
            t3 = clock()
            ev = self.features.update(ts, det, kps, lms)
            t4 = clock()
            audio = self._recent_audio(ts)
            label = self.rules.update(ev, audio, self._treat_recent(ts))
            t5 = clock()
        ok, buf = cv2.imencode(".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, 80])
        t6 = clock()
        if ok:
            self._jpeg = buf.tobytes()
        now = time.monotonic()
        self._last_frame_mono = now
        self._frame_times.append(now)

        for stage, ms in (("detect", t1 - t0), ("pose", t2 - t1), ("face", t3 - t2), ("features", t4 - t3),
                          ("rules", t5 - t4), ("jpeg", t6 - t5), ("total", t6 - t0)):
            self._stats.add(stage, ms * 1000)
        if not self.fast:
            self._stats.add("lag", (time.time() - ts) * 1000)  # capture -> event ready, incl. waiting for a turn

        if self.fast:
            self._flush_held_audio(ts)
        self._emit("frame", ev)
        self._emit("rules", label)
        if self.frame_tap is not None:
            try:
                self.frame_tap(frame, ev, label, audio)
            except Exception:
                self._limited("tap", "frame_tap raised", exc_info=True)

    def _recent_audio(self, ts: float) -> list[AudioEvent]:
        with self._lock:
            lo = bisect.bisect_left(self._audio_ts, ts - 5.0)
            hi = bisect.bisect_right(self._audio_ts, ts + AUDIO_LOOKAHEAD_S)
            return self._audio_events[lo:hi]

    def _treat_recent(self, ts: float) -> bool:
        with self._lock:
            return any(t <= ts <= t + self.treat_window_s for t in self._treats)

    def _flush_held_audio(self, upto_ts: float) -> None:
        while self._held_audio and self._held_audio[0].ts <= upto_ts:
            self._emit("audio", self._held_audio.popleft())

    # -- audio thread ------------------------------------------------------------------------

    def _audio_loop(self) -> None:
        try:
            for ts, chunk in self.src.audio():
                if self._stop.is_set():
                    break
                self._last_audio_mono = time.monotonic()
                t = time.perf_counter()
                events = self.audio.push(ts, chunk)
                if events:
                    self._stats.add("yamnet", (time.perf_counter() - t) * 1000)
                for ev in events:
                    with self._lock:
                        self._audio_events.append(ev)
                        self._audio_ts.append(ev.ts)
                        if not self.fast:
                            cut = bisect.bisect_left(self._audio_ts, ev.ts - AUDIO_HISTORY_S)
                            del self._audio_events[:cut], self._audio_ts[:cut]
                    if self.fast:
                        self._held_audio.append(ev)
                    else:
                        self._emit("audio", ev)
        except Exception:
            log.exception("audio source failed; continuing without audio")
        finally:
            self._audio_done.set()
            self._worker_finished()

    # -- watchdog and stats threads ----------------------------------------------------------

    def _watchdog_loop(self) -> None:
        """While a live source is stalled, report "no dog" so the rules fall to unknown."""
        while not self._stop.wait(WATCHDOG_TICK_S):
            if self._state(self._frame_age_s()) != "stalled":
                continue
            ts = time.time()
            with self._step:
                if self._state(self._frame_age_s()) != "stalled":
                    continue
                self._last_ts = max(self._last_ts, ts)
                ev = self.features.update(ts, None, None, None)
                label = self.rules.update(ev, self._recent_audio(ts), self._treat_recent(ts))
            self._emit("frame", ev)
            self._emit("rules", label)

    def _stats_loop(self) -> None:
        while not self._stop.wait(STATS_EVERY_S):
            took = self._stats.take()
            if not took:
                continue
            parts = []
            for stage in ("detect", "pose", "face", "features", "rules", "jpeg", "total", "yamnet", "lag"):
                v = took.get(stage)
                if v:
                    parts.append(f"{stage} {np.mean(v):.0f}/{np.percentile(v, 95):.0f}ms")
            extra = f", {self._outbox_dropped} events dropped" if self._outbox_dropped else ""
            log.info("pipeline %.1f fps | mean/p95: %s%s", self._fps(), " ".join(parts), extra)

    # -- misc ----------------------------------------------------------------------------------

    def _limited(self, key: str, msg: str, *args: Any, exc_info: bool = False) -> None:
        """log.error at most once per STATS_EVERY_S per key."""
        now = time.monotonic()
        if now - self._limit_last.get(key, -1e9) >= STATS_EVERY_S:
            self._limit_last[key] = now
            log.error(msg, *args, exc_info=exc_info)
