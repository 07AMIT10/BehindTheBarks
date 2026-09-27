"""Overlay ESC-50 dog sounds onto a video with ffmpeg and write a fallback clip.

    python scripts/mux_audio.py --list-dog
    python scripts/mux_audio.py data/fallback/raw/treat.mp4 --add 1-100032-A-0@4.5 --add 1-110389-A-0@7
    python scripts/mux_audio.py data/fallback/raw/idle.mp4            # no overlay: just normalise to mp4

--add FILE@SECONDS mixes FILE in so its first sound lands SECONDS into the video. FILE is a path, or an
ESC-50 filename / bare id resolved in --esc50-dir ("1-100032-A-0" and "1-100032-A-0.wav" both work).
Leading silence in the added clip is trimmed (--no-trim keeps it), so SECONDS is when the dog is heard.
The video's own audio, if it has any, stays under the overlay at --orig-volume (only when something is
overlaid; a plain transcode keeps it at full volume). Output goes to data/fallback/clips/<name>.mp4:
H.264 + AAC, constant 30 fps, long side capped at --max-side, so OpenCV and the browser read it the same.
"""

from __future__ import annotations

import argparse
import csv
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DOG_TARGET = "0"  # ESC-50 class index of "dog"
SILENCE_TRIM = "silenceremove=start_periods=1:start_threshold=-50dB:start_silence=0.05"


def resolve_esc50(name: str, esc50_dir: Path) -> Path:
    """A path, an ESC-50 filename, or a bare ESC-50 id -> an existing file."""
    direct = Path(name)
    if direct.is_file():
        return direct
    for cand in (esc50_dir / name, esc50_dir / f"{name}.wav"):
        if cand.is_file():
            return cand
    raise ValueError(f"audio {name!r} not found (looked for a file and in {esc50_dir})")


def parse_add(spec: str, esc50_dir: Path) -> tuple[Path, float]:
    """'FILE@SECONDS' -> (path, seconds)."""
    name, sep, at = spec.rpartition("@")
    if not sep or not name:
        raise ValueError(f"--add {spec!r}: expected FILE@SECONDS")
    try:
        seconds = float(at)
    except ValueError:
        raise ValueError(f"--add {spec!r}: {at!r} is not a number of seconds") from None
    if seconds < 0:
        raise ValueError(f"--add {spec!r}: seconds must be >= 0")
    return resolve_esc50(name, esc50_dir), seconds


def probe(path: Path) -> dict:
    """{'duration': seconds, 'has_audio': bool, 'width', 'height'} via ffprobe."""
    proc = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration:stream=codec_type,width,height",
         "-of", "json", str(path)], capture_output=True, text=True)
    if proc.returncode != 0:
        raise RuntimeError(f"ffprobe could not read {path}: {proc.stderr.strip()[:300]}")
    info = json.loads(proc.stdout)
    streams = info.get("streams", [])
    video = next((s for s in streams if s.get("codec_type") == "video"), None)
    if video is None:
        raise RuntimeError(f"{path} has no video stream")
    return {
        "duration": float(info.get("format", {}).get("duration") or 0.0),
        "has_audio": any(s.get("codec_type") == "audio" for s in streams),
        "width": video.get("width"), "height": video.get("height"),
    }


def build_filter(has_orig_audio: bool, delays_s: list[float], orig_volume: float, overlay_volume: float,
                 trim: bool, max_side: int, fps: int) -> tuple[str, bool]:
    """ffmpeg -filter_complex graph and whether it produces an audio output [a].

    Input 0 is the video; inputs 1..n are the overlays, delayed to delays_s[i-1].
    """
    cap = (f"scale='if(gt(iw,ih),min({max_side},iw),-2)':'if(gt(iw,ih),-2,min({max_side},ih))'"
           if max_side > 0 else "scale=trunc(iw/2)*2:trunc(ih/2)*2")
    parts = [f"[0:v]fps={fps},{cap},format=yuv420p[v]"]
    norm = "aresample=48000,aformat=channel_layouts=stereo"
    labels = []
    if has_orig_audio:
        vol = orig_volume if delays_s else 1.0
        parts.append(f"[0:a]{norm},volume={vol}[a0]")
        labels.append("[a0]")
    for i, delay in enumerate(delays_s, start=1):
        ms = int(round(delay * 1000))
        chain = [SILENCE_TRIM] if trim else []
        chain += [norm, f"adelay={ms}|{ms}", f"volume={overlay_volume}"]
        parts.append(f"[{i}:a]{','.join(chain)}[a{i}]")
        labels.append(f"[a{i}]")
    if not labels:
        return ";".join(parts), False
    mix = (f"{''.join(labels)}amix=inputs={len(labels)}:normalize=0:duration=longest:dropout_transition=0"
           if len(labels) > 1 else labels[0] + "anull")
    parts.append(f"{mix},alimiter=limit=0.95,apad[a]")  # pad so -shortest ends the file with the video
    return ";".join(parts), True


def mux(video: Path, adds: list[tuple[Path, float]], out: Path, *, orig_volume: float = 0.3,
        overlay_volume: float = 1.0, trim: bool = True, max_side: int = 1280, fps: int = 30) -> dict:
    """Run ffmpeg; returns the probe of the output."""
    if out.resolve() == video.resolve():
        raise ValueError("output would overwrite the input video")
    info = probe(video)
    for path, at in adds:
        if info["duration"] and at >= info["duration"]:
            raise ValueError(f"{path.name}@{at}: the video is only {info['duration']:.1f} s long")
    graph, has_audio = build_filter(info["has_audio"], [at for _, at in adds], orig_volume, overlay_volume,
                                    trim, max_side, fps)
    cmd = ["ffmpeg", "-y", "-v", "error", "-i", str(video)]
    for path, _ in adds:
        cmd += ["-i", str(path)]
    cmd += ["-filter_complex", graph, "-map", "[v]"]
    cmd += ["-map", "[a]", "-c:a", "aac", "-b:a", "128k", "-shortest"] if has_audio else ["-an"]
    cmd += ["-c:v", "libx264", "-preset", "medium", "-crf", "23", "-movflags", "+faststart", str(out)]
    out.parent.mkdir(parents=True, exist_ok=True)
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True)
    except FileNotFoundError:
        raise RuntimeError("ffmpeg not found on PATH (brew install ffmpeg)") from None
    if proc.returncode != 0:
        raise RuntimeError(f"ffmpeg failed: {proc.stderr.strip()[-600:]}")
    return probe(out)


def list_dog_clips(esc50_dir: Path) -> list[str]:
    meta = esc50_dir / "esc50.csv"
    if not meta.is_file():
        meta = esc50_dir / "meta" / "esc50.csv"
    if not meta.is_file():
        raise RuntimeError(f"no esc50.csv in {esc50_dir}")
    with meta.open() as fh:
        return sorted(row["filename"] for row in csv.DictReader(fh) if row["target"] == DOG_TARGET)


def parse_args(argv):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("video", nargs="?", type=Path, help="input video (usually in data/fallback/raw/)")
    ap.add_argument("--add", action="append", default=[], metavar="FILE@SECONDS",
                    help="overlay this audio so it starts SECONDS into the video (repeatable)")
    ap.add_argument("--out", type=Path, help="default: data/fallback/clips/<video name>.mp4")
    ap.add_argument("--orig-volume", type=float, default=0.3, help="original audio level under an overlay (default 0.3)")
    ap.add_argument("--overlay-volume", type=float, default=1.0, help="level of the added audio (default 1.0)")
    ap.add_argument("--no-trim", action="store_true", help="keep leading silence in the added clips")
    ap.add_argument("--max-side", type=int, default=1280, help="cap the long side in px, 0 = keep (default 1280)")
    ap.add_argument("--fps", type=int, default=30, help="constant output frame rate (default 30)")
    ap.add_argument("--esc50-dir", type=Path, default=ROOT / "data" / "esc50")
    ap.add_argument("--list-dog", action="store_true", help="list the ESC-50 dog clips and exit")
    return ap.parse_args(argv)


def main(argv=None) -> None:
    args = parse_args(argv)
    try:
        if args.list_dog:
            print("\n".join(list_dog_clips(args.esc50_dir)))
            return
        if args.video is None:
            raise SystemExit("give a VIDEO (or --list-dog)")
        adds = [parse_add(spec, args.esc50_dir) for spec in args.add]
        out = args.out or ROOT / "data" / "fallback" / "clips" / f"{args.video.stem}.mp4"
        res = mux(args.video, adds, out, orig_volume=args.orig_volume, overlay_volume=args.overlay_volume,
                  trim=not args.no_trim, max_side=args.max_side, fps=args.fps)
    except (ValueError, RuntimeError) as exc:
        sys.exit(f"error: {exc}")
    print(f"{out}  {res['width']}x{res['height']}  {res['duration']:.1f} s  audio={'yes' if res['has_audio'] else 'no'}"
          f"  overlays={len(adds)}")


if __name__ == "__main__":
    main()
