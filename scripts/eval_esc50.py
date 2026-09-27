"""Evaluate the audio-event detector on ESC-50 (dog clips vs a sample of non-dog clips).

    python scripts/eval_esc50.py                  # 40 dog clips + 8 per non-dog class
    python scripts/eval_esc50.py --per-class 20   # bigger false-positive sample

Downloads only the clips it needs from GitHub into data/esc50/ (gitignored), decodes them to 16 kHz
mono with ffmpeg, streams each through AudioEventDetector in 0.25 s chunks and reports:
  * dog clips with a `bark` event, and with any dog-family event (bark/yip/growl/whimper/howl)
  * non-dog clips that triggered a dog-family event (false positives), with label and score
ESC-50 is CC BY-NC 3.0; https://github.com/karolpiczak/ESC-50
"""

from __future__ import annotations

import argparse
import csv
import random
import sys
import urllib.request
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from backend.audio.yamnet_events import DOG_LABELS, SR, AudioEventDetector  # noqa: E402
from backend.sources import _decode_audio  # noqa: E402

BASE = "https://raw.githubusercontent.com/karolpiczak/ESC-50/master/audio/"
CACHE = ROOT / "data" / "esc50"


def fetch(name: str) -> Path:
    dest = CACHE / name
    if not dest.exists():
        urllib.request.urlretrieve(BASE + name, dest)
    return dest


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    p.add_argument("--per-class", type=int, default=8, help="non-dog clips sampled per class")
    p.add_argument("--seed", type=int, default=0)
    p.add_argument("--config", default=str(ROOT / "config.yaml"))
    args = p.parse_args()

    cfg = yaml.safe_load(Path(args.config).read_text())
    CACHE.mkdir(parents=True, exist_ok=True)
    meta_path = CACHE / "esc50.csv"
    if not meta_path.exists():
        urllib.request.urlretrieve(BASE.replace("audio/", "meta/esc50.csv"), meta_path)
    rows = list(csv.DictReader(meta_path.open()))

    by_cat: dict[str, list[str]] = defaultdict(list)
    for r in rows:
        by_cat[r["category"]].append(r["filename"])
    rng = random.Random(args.seed)
    dog = sorted(by_cat.pop("dog"))
    others = [(cat, f) for cat, files in sorted(by_cat.items()) for f in rng.sample(sorted(files), min(args.per_class, len(files)))]

    with ThreadPoolExecutor(8) as pool:
        list(pool.map(fetch, dog + [f for _, f in others]))

    det = AudioEventDetector(cfg)
    chunk = SR // 4

    def run(name: str) -> list:
        det.reset()
        data = _decode_audio(CACHE / name, required=True)
        events = []
        for i in range(0, len(data), chunk):
            events += det.push(i / SR, data[i : i + chunk])
        return [e for e in events if e.label in DOG_LABELS]

    dog_hits = {n: run(n) for n in dog}
    bark = sum(any(e.label == "bark" for e in ev) for ev in dog_hits.values())
    anydog = sum(bool(ev) for ev in dog_hits.values())
    print(f"dog clips: {len(dog)}")
    print(f"  bark detected:        {bark}/{len(dog)} ({bark / len(dog):.0%})")
    print(f"  any dog-family label: {anydog}/{len(dog)} ({anydog / len(dog):.0%})")
    print(f"  labels: {dict(Counter(e.label for ev in dog_hits.values() for e in ev))}")
    for n, ev in dog_hits.items():
        if not any(e.label == "bark" for e in ev):
            print(f"  no bark: {n} -> {[(e.label, round(e.score, 2)) for e in ev] or 'nothing'}")

    fps = []
    for cat, n in others:
        ev = run(n)
        if ev:
            fps.append((cat, n, max(ev, key=lambda e: e.score)))
    print(f"\nnon-dog clips: {len(others)} ({args.per_class} per class, {len(set(c for c, _ in others))} classes)")
    print(f"  false positives: {len(fps)}/{len(others)} ({len(fps) / len(others):.1%})")
    print(f"  by class: {dict(Counter(c for c, _, _ in fps))}")
    for cat, n, e in sorted(fps, key=lambda t: -t[2].score)[:12]:
        print(f"  {cat:18s} {n:22s} {e.label:8s} {e.score:.2f}")


if __name__ == "__main__":
    main()
