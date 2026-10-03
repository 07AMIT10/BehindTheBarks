"""Precompute events.jsonl + a debug video for every fallback clip, and keep data/fallback/manifest.json in sync.

    python scripts/precompute_events.py                    # every clip in data/fallback/clips/
    python scripts/precompute_events.py --only german_shepherd_treat --no-debug
    python scripts/precompute_events.py --manifest-only    # refresh durations/paths, run no models

Each clip runs through scripts/run_pipeline.py in its own process (--source file --fast --rebase-ts), so
every events file is deterministic and its timestamps are seconds from the clip start; treat lines sit at
the manifest's `treats` times. Output: data/fallback/events/<clip>.jsonl and <clip>.debug.mp4.

The manifest is edited by hand as well as by this script. The script owns `clip`, `events`, `debug_video`,
`duration_s`, `frames` and `observed`, and never touches `expected_emotion`, `treats` or `notes`.
Paths in the manifest are relative to the manifest's own folder.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from backend.contracts import EMOTIONS, AudioEvent, FrameEvent, RulesLabel  # noqa: E402
from scripts.mux_audio import probe  # noqa: E402

FALLBACK = ROOT / "data" / "fallback"
CLIP_SUFFIXES = {".mp4", ".webm", ".mov", ".mkv"}
MANIFEST_VERSION = 1
MODELS = {"frame": FrameEvent, "audio": AudioEvent, "rules": RulesLabel}


def find_clips(clips_dir: Path, only: list[str] | None = None) -> list[Path]:
    clips = sorted(p for p in clips_dir.glob("*") if p.suffix.lower() in CLIP_SUFFIXES)
    if only:
        wanted = set(only)
        missing = wanted - {p.stem for p in clips}
        if missing:
            raise SystemExit(f"no such clip in {clips_dir}: {', '.join(sorted(missing))}")
        clips = [p for p in clips if p.stem in wanted]
    return clips


def load_manifest(path: Path) -> dict:
    if path.is_file():
        data = json.loads(path.read_text())
        data.setdefault("clips", [])
        return data
    return {"version": MANIFEST_VERSION, "ts_origin": "clip_start", "clips": []}


def save_manifest(path: Path, manifest: dict) -> None:
    manifest["clips"].sort(key=lambda c: c["name"])
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(manifest, indent=2) + "\n")
    tmp.replace(path)


def upsert(manifest: dict, name: str) -> dict:
    """The manifest entry for `name`, created with hand-filled fields left for the user."""
    for entry in manifest["clips"]:
        if entry["name"] == name:
            return entry
    entry = {"name": name, "expected_emotion": None, "treats": [], "notes": ""}
    manifest["clips"].append(entry)
    return entry


def read_events(path: Path) -> tuple[list[dict], dict[str, int]]:
    """Parse and validate an events.jsonl against the contracts; returns (rules labels, counts per type)."""
    rules: list[dict] = []
    counts = {"frame": 0, "audio": 0, "rules": 0, "treat": 0}
    with path.open() as fh:
        for n, line in enumerate(fh, start=1):
            rec = json.loads(line)
            kind, data = rec["type"], rec["data"]
            if kind == "treat":
                if not isinstance(data.get("ts"), (int, float)):
                    raise ValueError(f"{path}:{n}: treat without a ts")
            elif kind in MODELS:
                MODELS[kind].model_validate(data)
            else:
                raise ValueError(f"{path}:{n}: unknown event type {kind!r}")
            counts[kind] += 1
            if kind == "rules":
                rules.append(data)
    return rules, counts


def observed_emotions(rules: list[dict], duration_s: float) -> dict:
    """Seconds each rules label was on show (a label holds until the next one) and the dominant one.

    `unknown` never counts as dominant unless nothing else was ever shown.
    """
    seconds = {e: 0.0 for e in EMOTIONS}
    for i, label in enumerate(rules):
        end = rules[i + 1]["ts"] if i + 1 < len(rules) else max(duration_s, label["ts"])
        seconds[label["emotion"]] += max(0.0, end - label["ts"])
    known = {e: s for e, s in seconds.items() if e != "unknown" and s > 0}
    dominant = max(known, key=known.get) if known else "unknown"
    return {"dominant": dominant, "seconds": {e: round(s, 1) for e, s in seconds.items() if s > 0}}


def problems(entry: dict) -> list[str]:
    """Hand-filled fields that are wrong (reported, never fixed)."""
    out = []
    exp = entry.get("expected_emotion")
    if exp is not None and exp not in EMOTIONS:
        out.append(f"expected_emotion {exp!r} is not in the vocabulary")
    dur = entry.get("duration_s")
    for t in entry.get("treats", []):
        if not isinstance(t, (int, float)) or t < 0 or (dur is not None and t > dur):
            out.append(f"treat at {t!r} is outside the clip (0..{dur} s)")
    return out


def run_clip(clip: Path, entry: dict, events_dir: Path, config: str, debug: bool, profile: str = "server") -> Path:
    events = events_dir / f"{clip.stem}.jsonl"
    cmd = [sys.executable, str(ROOT / "scripts" / "run_pipeline.py"), "--config", config,
           "--source", "file", "--path", str(clip), "--fast", "--rebase-ts", "--jsonl", str(events),
           "--profile", profile]
    if debug:
        cmd += ["--debug-video", str(events_dir / f"{clip.stem}.debug.mp4")]
    for t in entry.get("treats", []):
        cmd += ["--treat-at", str(t)]
    proc = subprocess.run(cmd, cwd=ROOT, capture_output=True, text=True)
    if proc.returncode != 0:
        raise RuntimeError(f"run_pipeline failed for {clip.name}:\n{proc.stderr.strip()[-800:]}")
    return events


def rel(path: Path, base: Path) -> str:
    return path.resolve().relative_to(base.resolve()).as_posix()


def parse_args(argv):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--clips-dir", type=Path, default=FALLBACK / "clips")
    ap.add_argument("--events-dir", type=Path, default=FALLBACK / "events")
    ap.add_argument("--out", type=Path, help="alias for --events-dir")
    ap.add_argument("--profile", choices=["server", "mobile"], default="server", help="perception profile")
    ap.add_argument("--manifest", type=Path, default=FALLBACK / "manifest.json")
    ap.add_argument("--config", default=str(ROOT / "config.yaml"))
    ap.add_argument("--only", action="append", metavar="NAME", help="clip name without extension (repeatable)")
    ap.add_argument("--no-debug", action="store_true", help="skip the annotated debug videos")
    ap.add_argument("--manifest-only", action="store_true", help="refresh the manifest; run no models")
    return ap.parse_args(argv)


def main(argv=None) -> None:
    args = parse_args(argv)
    clips = find_clips(args.clips_dir, args.only)
    if not clips:
        raise SystemExit(f"no clips in {args.clips_dir} (put them there with scripts/mux_audio.py)")
    base = args.manifest.parent
    events_dir = args.out or args.events_dir
    events_dir.mkdir(parents=True, exist_ok=True)
    manifest = load_manifest(args.manifest)
    failed = []

    for clip in clips:
        entry = upsert(manifest, clip.stem)
        entry["clip"] = rel(clip, base)
        entry["duration_s"] = round(probe(clip)["duration"], 2)
        for msg in problems(entry):
            print(f"  warning: {clip.stem}: {msg}")
        if not args.manifest_only:
            print(f"{clip.stem}: {entry['duration_s']} s, treats at {entry['treats'] or 'none'} ...", flush=True)
            t0 = time.time()
            try:
                events = run_clip(clip, entry, events_dir, args.config, debug=not args.no_debug, profile=args.profile)
                rules, counts = read_events(events)
            except (RuntimeError, ValueError, OSError) as exc:
                print(f"  FAILED: {exc}")
                failed.append(clip.stem)
                save_manifest(args.manifest, manifest)
                continue
            entry["events"] = rel(events, base)
            debug_video = events_dir / f"{clip.stem}.debug.mp4"
            if not args.no_debug and debug_video.is_file():
                entry["debug_video"] = rel(debug_video, base)
            else:
                entry.pop("debug_video", None)
            entry["frames"] = counts["frame"]
            entry["observed"] = observed_emotions(rules, entry["duration_s"])
            exp = entry["expected_emotion"]
            note = "" if exp is None else ("  (matches expected)" if exp == entry["observed"]["dominant"]
                                           else f"  (expected {exp})")
            print(f"  {counts['frame']} frames, {counts['audio']} audio, {counts['treat']} treats in {time.time() - t0:.0f} s;"
                  f" dominant: {entry['observed']['dominant']}{note}")
        if events_dir == FALLBACK / "events" and args.profile == "server":
            save_manifest(args.manifest, manifest)

    todo = [c["name"] for c in manifest["clips"] if c.get("expected_emotion") is None]
    print(f"manifest: {args.manifest}  ({len(manifest['clips'])} clips)")
    if todo:
        print(f"  fill in expected_emotion (and treats) for: {', '.join(todo)}")
    if failed:
        sys.exit(f"failed: {', '.join(failed)}")


if __name__ == "__main__":
    main()
