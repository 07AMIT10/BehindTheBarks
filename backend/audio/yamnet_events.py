"""YAMNet audio events: 16 kHz mono chunks -> AudioEvent.

    det = AudioEventDetector(cfg)                    # reads `data.audio`; cfg is the parsed config.yaml
    for ts, chunk in src.audio():                    # MediaSources.audio(): mono float32, 16 kHz
        for event in det.push(ts, chunk): ...        # AudioEvent objects, possibly none per chunk

Chunks of any size are buffered into 0.96 s windows (YAMNet's native patch) with a hop of `hop_s`
(default 0.48 s, 50% overlap). Each window is scored once and mapped to one of our labels:

    bark / yip / growl / whimper / howl   max score over the AudioSet classes listed in `label_map`,
                                          if the best label reaches `threshold`
    silence                               window RMS below `silence_rms` (the model is not run)
    other                                 everything else

`AudioEvent.ts` is the END of the window (when the sound became knowable), on the same epoch clock
as FrameEvent.ts. Its score is the mapped-class score for dog labels, the top non-dog class score
for "other", and 1 - rms/silence_rms for "silence".

Debounce: overlapping windows would report one bark two or three times. A dog label is emitted at
most once per `debounce_s`; silence/other are emitted only when they differ from the previous window.
Set `debounce_s: 0` to get one event per window.

The model is loaded from `model_dir`. If it is missing it is downloaded once from TensorFlow Hub
(network needed that one time) and copied there, so the offline demo never touches the network.
"""

from __future__ import annotations

import argparse
import csv
import logging
import os
import shutil
import tempfile
from functools import lru_cache
from pathlib import Path
from typing import Any, Iterable, Iterator

import numpy as np

from backend.contracts import AudioEvent

log = logging.getLogger(__name__)

SR = 16_000
WINDOW = 15_600  # 0.975 s: exactly one YAMNet score frame (patch_window 0.96 s + STFT padding)
HUB_URL = "https://tfhub.dev/google/yamnet/1"
DOG_LABELS = ("bark", "yip", "growl", "whimper", "howl")

DEFAULTS: dict[str, Any] = {
    "model_dir": "models/yamnet",
    "hop_s": 0.48,
    "threshold": 0.3,
    "silence_rms": 0.005,
    "debounce_s": 1.0,
    "max_gap_s": 0.5,
    "label_map": {
        "bark": ["Bark", "Bow-wow"],
        "yip": ["Yip"],
        "growl": ["Growling"],
        "whimper": ["Whimper (dog)"],
        "howl": ["Howl"],
    },
}


@lru_cache(maxsize=2)
def load_yamnet(model_dir: str) -> tuple[Any, list[str]]:
    """Load YAMNet once per process. Returns (callable model, 521 class display names)."""
    os.environ.setdefault("TF_CPP_MIN_LOG_LEVEL", "2")
    import tensorflow as tf

    path = Path(model_dir)
    if not (path / "saved_model.pb").exists():
        import tensorflow_hub as hub

        log.info("YAMNet not found in %s, downloading from TF Hub (one time)", path)
        with tempfile.TemporaryDirectory() as tmp:
            os.environ["TFHUB_CACHE_DIR"] = tmp
            src = Path(hub.resolve(HUB_URL))
            path.parent.mkdir(parents=True, exist_ok=True)
            shutil.copytree(src, path, dirs_exist_ok=True)
    model = tf.saved_model.load(str(path))
    with open(path / "assets" / "yamnet_class_map.csv", newline="") as f:
        names = [row["display_name"] for row in csv.DictReader(f)]
    return model, names


class AudioEventDetector:
    def __init__(self, cfg: dict | None = None):
        """`cfg` is the parsed config.yaml (or None for defaults); reads `data.audio`."""
        c = {**DEFAULTS, **((cfg or {}).get("data", {}).get("audio") or {})}
        self.threshold = float(c["threshold"])
        self.silence_rms = float(c["silence_rms"])
        self.debounce_s = float(c["debounce_s"])
        self.max_gap_s = float(c["max_gap_s"])
        self.hop = max(1, int(round(float(c["hop_s"]) * SR)))
        self._model, names = load_yamnet(str(c["model_dir"]))

        index = {n: i for i, n in enumerate(names)}
        self._label_idx: dict[str, list[int]] = {}
        for label, classes in c["label_map"].items():
            if label not in DOG_LABELS:
                raise ValueError(f"label_map key {label!r} is not one of {DOG_LABELS}")
            missing = [n for n in classes if n not in index]
            if missing:
                raise ValueError(f"label_map[{label}]: unknown AudioSet classes {missing}")
            self._label_idx[label] = [index[n] for n in classes]
        mapped = {i for idx in self._label_idx.values() for i in idx}
        self._other_idx = np.array([i for i in range(len(names)) if i not in mapped])
        self.reset()

    def reset(self) -> None:
        """Forget buffered audio and debounce state (call between unrelated clips)."""
        self._buf = np.zeros(0, dtype=np.float32)
        self._buf_ts = 0.0  # timestamp of _buf[0]
        self._last_window_label: str | None = None
        self._last_emit: dict[str, float] = {}

    def push(self, ts: float, chunk: np.ndarray) -> list[AudioEvent]:
        """Feed one chunk (mono float32 16 kHz, `ts` = time of its first sample); returns new events."""
        chunk = np.asarray(chunk, dtype=np.float32).reshape(-1)
        if len(self._buf):
            expected = self._buf_ts + len(self._buf) / SR
            if abs(ts - expected) > self.max_gap_s:  # dropped chunks: don't stitch across the hole
                self._buf = np.zeros(0, dtype=np.float32)
        if not len(self._buf):
            self._buf_ts = ts
        self._buf = np.concatenate([self._buf, chunk])

        events: list[AudioEvent] = []
        while len(self._buf) >= WINDOW:
            ev = self._process_window(self._buf[:WINDOW], self._buf_ts + WINDOW / SR)
            if ev is not None:
                events.append(ev)
            self._buf = self._buf[self.hop :]
            self._buf_ts += self.hop / SR
        return events

    # -- internals ---------------------------------------------------------------------------

    def _classify(self, window: np.ndarray) -> tuple[str, float]:
        rms = float(np.sqrt(np.mean(window.astype(np.float64) ** 2)))
        if rms < self.silence_rms:
            return "silence", float(np.clip(1.0 - rms / self.silence_rms, 0.0, 1.0))
        scores = np.asarray(self._model(window)[0])
        scores = scores.max(axis=0) if scores.ndim == 2 else scores  # (frames, 521) -> (521,)
        per_label = {lab: float(scores[idx].max()) for lab, idx in self._label_idx.items()}
        best = max(per_label, key=per_label.get) if per_label else None
        if best is not None and per_label[best] >= self.threshold:
            return best, float(np.clip(per_label[best], 0.0, 1.0))
        return "other", float(np.clip(scores[self._other_idx].max(), 0.0, 1.0))

    def _process_window(self, window: np.ndarray, ts: float) -> AudioEvent | None:
        label, score = self._classify(window)
        prev, self._last_window_label = self._last_window_label, label
        if label in DOG_LABELS:
            last = self._last_emit.get(label)
            if last is not None and ts - last < self.debounce_s:
                return None
            self._last_emit[label] = ts
        elif label == prev and self.debounce_s > 0:
            return None
        return AudioEvent(ts=ts, label=label, score=score)


def audio_events(chunks: Iterable[tuple[float, np.ndarray]], cfg: dict | None = None) -> Iterator[AudioEvent]:
    """Convenience wrapper: turn a (ts, chunk) iterator such as MediaSources.audio() into events."""
    det = AudioEventDetector(cfg)
    for ts, chunk in chunks:
        yield from det.push(ts, chunk)


# -- CLI -------------------------------------------------------------------------------------


def main(argv: list[str] | None = None) -> None:
    import yaml

    from backend.sources import MediaSources

    p = argparse.ArgumentParser(description="Print AudioEvents from a file or the microphone.")
    p.add_argument("--config", default="config.yaml")
    p.add_argument("--source", choices=["webcam", "stream", "file"])
    p.add_argument("--path", help="audio/video file path or stream URL")
    p.add_argument("--audio", help='audio override: a file path or "mic"')
    p.add_argument("--fast", action="store_true", help="file mode: process as fast as possible")
    args = p.parse_args(argv)

    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    cfg = yaml.safe_load(Path(args.config).read_text()) if Path(args.config).exists() else {"data": {"source": {}}}
    src = MediaSources.from_config(cfg, type=args.source, path=args.path, audio=args.audio, fast=args.fast)
    try:
        for ev in audio_events(src.audio(), cfg):
            print(f"t={ev.ts:.2f}  {ev.label:8s} {ev.score:.2f}", flush=True)
    except KeyboardInterrupt:
        pass
    finally:
        src.close()


if __name__ == "__main__":
    main()
