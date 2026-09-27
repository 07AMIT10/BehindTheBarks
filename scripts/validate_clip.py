"""Validate a fallback-clip manifest and its events.jsonl files against the contracts.

    .venv/bin/python scripts/validate_clip.py [manifest.json]   # default: web.demo.manifest from config.yaml

Accepts Person A's schema (entries with clip/events paths, lines {"type","data"} with time in
data.ts) and the web placeholder schema (entries with dir, lines {"t","type","data"}).
Exit 0 when valid, 1 with one error per line otherwise.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from pydantic import ValidationError  # noqa: E402

from backend.contracts import EMOTIONS, AudioEvent, FrameEvent, RulesLabel  # noqa: E402
from backend.demo.clips import normalize_line  # noqa: E402

BY_TYPE = {"frame": FrameEvent, "audio": AudioEvent, "rules": RulesLabel}


def _resolve(base: Path, c: dict) -> tuple[str, Path, Path, str | None] | None:
    """-> (clip_id, video_path, events_path, emotion) or None if the entry shape is unknown."""
    if not isinstance(c, dict):
        return None
    if "events" in c and "clip" in c:
        return (str(c.get("name", c["clip"])), base / c["clip"], base / c["events"],
                c.get("expected_emotion"))
    if "id" in c and "dir" in c:
        d = base / c["dir"]
        return (str(c["id"]), d / "clip.mp4", d / "events.jsonl", c.get("emotion"))
    return None


def _errs(manifest_path: Path) -> list[str]:
    errs: list[str] = []
    try:
        manifest = json.loads(manifest_path.read_text())
    except (OSError, ValueError) as exc:
        return [f"manifest {manifest_path}: unreadable ({exc})"]
    clips = manifest.get("clips")
    if not isinstance(clips, list) or not clips:
        return [f"manifest {manifest_path}: 'clips' must be a non-empty list"]
    base = manifest_path.parent
    for i, c in enumerate(clips):
        where = f"manifest clip #{i}"
        resolved = _resolve(base, c)
        if resolved is None:
            errs.append(f"{where}: unknown entry shape (need clip+events or id+dir)")
            continue
        cid, video, events, emotion = resolved
        if emotion not in EMOTIONS:
            errs.append(f"{where}: emotion {emotion!r} not in the fixed vocabulary")
        if not video.exists():
            errs.append(f"clip {cid}: missing video {video}")
        if not events.exists():
            errs.append(f"clip {cid}: missing events file {events}")
            continue
        for n, line in enumerate(events.read_text().splitlines(), 1):
            if not line.strip():
                continue
            try:
                obj = json.loads(line)
            except ValueError:
                errs.append(f"clip {cid} line {n}: not JSON")
                continue
            if not isinstance(obj, dict) or obj.get("type") == "treat":
                if not isinstance(obj, dict):
                    errs.append(f"clip {cid} line {n}: not an object")
                continue
            norm = normalize_line(obj)
            if norm is None:
                errs.append(f"clip {cid} line {n}: needs a type and a numeric t (or data.ts)")
                continue
            model = BY_TYPE.get(norm["type"])
            if model is None:
                errs.append(f"clip {cid} line {n}: type {norm['type']!r} must be frame|audio|rules|treat")
                continue
            try:
                model.model_validate(norm["data"])
            except ValidationError as exc:
                errs.append(f"clip {cid} line {n}: {exc.errors()[0]['loc']}: {exc.errors()[0]['msg']}")
    return errs


def validate_clip(manifest_path: str | Path) -> list[str]:
    return _errs(Path(manifest_path))


def main(argv: list[str] | None = None) -> int:
    args = list(argv if argv is not None else sys.argv[1:])
    if args:
        path = Path(args[0])
    else:
        sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
        from backend.web.settings import load_config

        path = Path(load_config()["web"]["demo"]["manifest"])
    errs = validate_clip(path)
    for e in errs:
        print(e)
    return 1 if errs else 0


if __name__ == "__main__":
    raise SystemExit(main())
