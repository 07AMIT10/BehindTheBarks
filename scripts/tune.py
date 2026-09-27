"""Tuning aid for backend/fusion/rules.py: a timeline plot and a match table per fallback clip.

    python scripts/tune.py                       # every clip in the manifest
    python scripts/tune.py --only vocal_chained   # one clip (repeatable)
    python scripts/tune.py --out-dir out/tuning

Reads data/fallback/events/<clip>.jsonl (built by scripts/precompute_events.py) and, per clip, saves a
PNG with: the continuous features, ear position / feeding-zone, audio events and treats, and the eight
per-emotion rule scores with the emitted label shaded behind them and the manifest's `expected_emotion`
in the title. Then prints one line per clip: expected vs. the time-weighted dominant label, and the
breakdown of what else the label held.

This only reads existing events; it does not run the pipeline. Re-run precompute_events.py first if
config.yaml or rules.py changed.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from backend.contracts import EMOTIONS  # noqa: E402
from scripts.precompute_events import load_manifest, observed_emotions  # noqa: E402

FALLBACK = ROOT / "data" / "fallback"
FEATURE_COLORS = {"tail_height": "tab:blue", "mouth_open": "tab:green", "body_lowering": "tab:red", "motion_energy": "tab:purple"}
EMOTION_COLORS = {  # matches scripts/run_pipeline.py's BGR palette, converted to RGB 0..1
    "happy": "#50c850", "excited": "#ffa500", "relaxed": "#c8c850", "anxious": "#dcc800",
    "fearful": "#c850c8", "aggressive": "#e63c3c", "disinterested": "#a0a0a0", "unknown": "#6e6e6e",
}


def load_events(path: Path) -> dict[str, list[dict]]:
    out: dict[str, list[dict]] = {"frame": [], "audio": [], "rules": [], "treat": []}
    with path.open() as fh:
        for line in fh:
            rec = json.loads(line)
            out[rec["type"]].append(rec["data"])
    return out


def label_breakdown(seconds: dict[str, float], duration: float) -> str:
    """'relaxed 70%, happy 16%, disinterested 14%', biggest share first, dropping zero shares."""
    if not seconds or duration <= 0:
        return "(no rules output)"
    parts = sorted(seconds.items(), key=lambda kv: -kv[1])
    return ", ".join(f"{e} {100 * s / duration:.0f}%" for e, s in parts if s > 0)


def plot_clip(name: str, entry: dict, events: dict[str, list[dict]], out_path: Path) -> dict:
    frames, audio, rules, treats = events["frame"], events["audio"], events["rules"], events["treat"]
    duration = entry.get("duration_s") or (frames[-1]["ts"] if frames else 0.0)
    obs = observed_emotions(rules, duration)

    fig, axes = plt.subplots(4, 1, figsize=(13, 10), sharex=True,
                             gridspec_kw={"height_ratios": [2.0, 0.6, 0.8, 2.4]})
    expected = entry.get("expected_emotion") or "?"
    match = "matches" if expected == obs["dominant"] else "expected " + expected
    fig.suptitle(f"{name}  —  dominant: {obs['dominant']} ({match})", fontsize=13)

    # 1. continuous features (tail_wag_hz is unbounded, so it gets its own axis)
    ax = axes[0]
    f_ts = [f["ts"] for f in frames]
    for key, color in FEATURE_COLORS.items():
        vals = [f["features"][key] for f in frames]
        ax.plot(f_ts, vals, label=key, color=color, marker=".", markersize=2, linewidth=1)
    ax2 = ax.twinx()
    ax2.plot(f_ts, [f["features"]["tail_wag_hz"] for f in frames], label="tail_wag_hz",
             color="black", linestyle="--", linewidth=1, alpha=0.6)
    ax.set_ylim(-1.05, 1.05), ax2.set_ylim(0, 6)
    ax.set_ylabel("feature"), ax2.set_ylabel("wag (Hz)")
    h1, l1 = ax.get_legend_handles_labels(); h2, l2 = ax2.get_legend_handles_labels()
    ax.legend(h1 + h2, l1 + l2, loc="upper left", fontsize=8, ncol=5)

    # 2. ear position (categorical) + feeding-zone band
    ax = axes[1]
    ear_num = {"up": 1.0, "neutral": 0.0, "back": -1.0}
    ax.step(f_ts, [ear_num.get(f["features"]["ear_position"], np.nan) for f in frames],
            where="post", color="tab:brown", label="ear (up/neutral/back)")
    zone = [1.0 if f["features"]["in_feeding_zone"] else 0.0 for f in frames]
    ax.fill_between(f_ts, 0, zone, step="post", alpha=0.15, color="green", label="in_feeding_zone")
    ax.set_ylim(-1.3, 1.3), ax.set_yticks([-1, 0, 1])
    ax.legend(loc="upper left", fontsize=8, ncol=2)

    # 3. audio events + treats
    ax = axes[2]
    if audio:
        a_ts, a_score = [a["ts"] for a in audio], [a["score"] for a in audio]
        ax.stem(a_ts, a_score, basefmt=" ")
        for x, y, a in zip(a_ts, a_score, audio):
            ax.annotate(a["label"], (x, y), fontsize=7, textcoords="offset points", xytext=(2, 2))
    for t in treats:
        ax.axvline(t["ts"], color="orange", linestyle=":", linewidth=1.5)
        ax.annotate("treat", (t["ts"], 0.95), fontsize=8, color="orange")
    ax.set_ylim(0, 1.05), ax.set_ylabel("audio score")

    # 4. per-emotion rule scores, with the emitted label shaded behind them
    ax = axes[3]
    r_ts = [r["ts"] for r in rules]
    span_start, span_label = (r_ts[0], rules[0]["emotion"]) if rules else (0.0, None)
    for r in rules[1:] + [{"ts": duration, "emotion": None}]:
        if r["emotion"] != span_label:
            ax.axvspan(span_start, r["ts"], color=EMOTION_COLORS.get(span_label, "gray"), alpha=0.08)
            span_start, span_label = r["ts"], r["emotion"]
    for e in EMOTIONS:
        ax.plot(r_ts, [r["scores"][e] for r in rules], label=e, color=EMOTION_COLORS[e],
                linewidth=1.6 if e == expected else 1.0, alpha=1.0 if e == expected else 0.75)
    ax.set_ylim(0, 1.05), ax.set_xlabel("time (s)"), ax.set_ylabel("rule score")
    ax.legend(loc="upper left", fontsize=7, ncol=4)

    fig.tight_layout()
    out_path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(out_path, dpi=110)
    plt.close(fig)
    return obs


def parse_args(argv):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--manifest", type=Path, default=FALLBACK / "manifest.json")
    ap.add_argument("--events-dir", type=Path, default=FALLBACK / "events")
    ap.add_argument("--out-dir", type=Path, default=FALLBACK / "tuning")
    ap.add_argument("--only", action="append", metavar="NAME", help="clip name (repeatable)")
    return ap.parse_args(argv)


def main(argv=None) -> None:
    args = parse_args(argv)
    manifest = load_manifest(args.manifest)
    clips = manifest["clips"]
    if args.only:
        wanted = set(args.only)
        missing = wanted - {c["name"] for c in clips}
        if missing:
            raise SystemExit(f"not in the manifest: {', '.join(sorted(missing))}")
        clips = [c for c in clips if c["name"] in wanted]
    if not clips:
        raise SystemExit(f"no clips in {args.manifest}")

    rows = []
    for entry in clips:
        events_path = args.events_dir / f"{entry['name']}.jsonl"
        if not events_path.is_file():
            print(f"  skip {entry['name']}: no {events_path} (run precompute_events.py)")
            continue
        events = load_events(events_path)
        if not events["frame"]:
            print(f"  skip {entry['name']}: events file has no frames")
            continue
        obs = plot_clip(entry["name"], entry, events, args.out_dir / f"{entry['name']}.png")
        expected = entry.get("expected_emotion")
        rows.append((entry["name"], expected or "?", obs["dominant"], expected == obs["dominant"],
                    label_breakdown(obs["seconds"], entry.get("duration_s", 0.0))))

    name_w = max((len(r[0]) for r in rows), default=4)
    print(f"\n{'clip':<{name_w}}  {'expected':<13}  {'dominant':<13}  match  breakdown")
    for name, expected, dominant, ok, breakdown in rows:
        print(f"{name:<{name_w}}  {expected:<13}  {dominant:<13}  {'yes' if ok else 'NO':<5}  {breakdown}")
    hits = sum(1 for r in rows if r[3])
    print(f"\n{hits}/{len(rows)} match  ->  {args.out_dir}/")


if __name__ == "__main__":
    main()
