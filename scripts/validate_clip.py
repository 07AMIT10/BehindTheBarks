"""Validate a fallback-clip manifest and its events.jsonl files against the contracts.

    .venv/bin/python scripts/validate_clip.py [manifest.json]   # default: web.demo.manifest from config.yaml

Exit 0 when valid, 1 with one error per line otherwise. Used by Person A before handing over clips.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from pydantic import ValidationError  # noqa: E402

from backend.contracts import EMOTIONS, AudioEvent, FrameEvent, RulesLabel  # noqa: E402

BY_TYPE = {"frame": FrameEvent, "audio": AudioEvent, "rules": RulesLabel}


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
        if not isinstance(c, dict):
            errs.append(f"{where}: not an object")
            continue
        for key in ("id", "name", "emotion", "dir"):
            if key not in c:
                errs.append(f"{where}: missing {key!r}")
        if c.get("emotion") not in EMOTIONS:
            errs.append(f"{where}: emotion {c.get('emotion')!r} not in the fixed vocabulary")
        d = base / str(c.get("dir", ""))
        for f in ("clip.mp4", "events.jsonl", "meta.json"):
            if not (d / f).exists():
                errs.append(f"clip {c.get('id')}: missing {f}")
        ev = d / "events.jsonl"
        if ev.exists():
            for n, line in enumerate(ev.read_text().splitlines(), 1):
                if not line.strip():
                    continue
                try:
                    obj = json.loads(line)
                except ValueError:
                    errs.append(f"clip {c.get('id')} line {n}: not JSON")
                    continue
                if not isinstance(obj, dict) or not isinstance(obj.get("t"), (int, float)):
                    errs.append(f"clip {c.get('id')} line {n}: needs numeric 't'")
                    continue
                t = obj["type"]
                if t == "treat":
                    continue
                model = BY_TYPE.get(t)
                if model is None:
                    errs.append(f"clip {c.get('id')} line {n}: type {t!r} must be frame|audio|rules|treat")
                    continue
                try:
                    model.model_validate(obj.get("data"))
                except ValidationError as exc:
                    errs.append(f"clip {c.get('id')} line {n}: {exc.errors()[0]['loc']}: {exc.errors()[0]['msg']}")
                else:
                    ts = (obj.get("data") or {}).get("ts")
                    if isinstance(ts, (int, float)) and abs(ts - obj["t"]) > 0.01:
                        errs.append(f"clip {c.get('id')} line {n}: data.ts should equal t (clip-relative)")
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
