"""Demo clip packages: manifest + <dir>/{clip.mp4, events.jsonl, meta.json}; timelines in demo/cache/."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

CACHE = Path(__file__).resolve().parent / "cache"


@dataclass
class ClipMeta:
    id: str
    name: str
    emotion: str
    duration_s: float
    has_timeline: bool


class DemoClips:
    def __init__(self, manifest: Path | None, bundled: Path) -> None:
        self._clips: dict[str, tuple[Path, dict]] = {}
        for src in ([manifest] if manifest and manifest.exists() else []) + [bundled / "manifest.json"]:
            try:
                doc = json.loads(Path(src).read_text())
            except (OSError, ValueError):
                continue
            if not isinstance(doc.get("clips"), list):
                continue
            for c in doc["clips"]:
                if not isinstance(c, dict) or "id" not in c or "dir" not in c:
                    continue
                d = Path(src).parent / c["dir"]
                if (d / "clip.mp4").exists() and (d / "events.jsonl").exists():
                    self._clips.setdefault(c["id"], (d, c))
            if self._clips:
                break

    @property
    def available(self) -> bool:
        return bool(self._clips)

    def list(self) -> list[ClipMeta]:
        out = []
        for cid, (d, c) in self._clips.items():
            try:
                meta = json.loads((d / "meta.json").read_text())
            except (OSError, ValueError):
                meta = {}
            out.append(ClipMeta(cid, meta.get("name", c.get("name", cid)), meta.get("emotion", c.get("emotion", "unknown")),
                                float(meta.get("duration_s", 0.0)), (CACHE / f"{cid}.timeline.json").exists()))
        return out

    def _dir(self, cid: str) -> Path:
        try:
            return self._clips[cid][0]
        except KeyError:
            raise KeyError(f"unknown demo clip {cid!r}") from None

    def video_path(self, cid: str) -> Path:
        return self._dir(cid) / "clip.mp4"

    def events(self, cid: str) -> list[dict]:
        return [json.loads(line) for line in (self._dir(cid) / "events.jsonl").read_text().splitlines() if line.strip()]

    def timeline(self, cid: str) -> dict | None:
        p = CACHE / f"{cid}.timeline.json"
        if not p.exists():
            return None
        return json.loads(p.read_text())


def load_demo_clips(cfg: dict) -> DemoClips:
    demo = cfg["web"]["demo"]
    return DemoClips(Path(demo["manifest"]), Path(demo["clips_dir"]))
