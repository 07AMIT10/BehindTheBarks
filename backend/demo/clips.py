"""Demo clip packages, in either manifest schema.

Person A style (data/fallback/manifest.json): {"clips": [{"name", "expected_emotion", "treats": [...],
"clip": "clips/x.mp4", "duration_s", "events": "events/x.jsonl", ...}]}; event lines are
{"type", "data"} with the time in data.ts (clip-relative via --rebase-ts).
Web style (backend/demo/clips/manifest.json): {"clips": [{"id", "name", "emotion", "dir"}]} with
<dir>/{clip.mp4, events.jsonl, meta.json}; event lines are {"t", "type", "data"}.

Everything downstream sees the normalized form: {"t", "type", "data"} with clip-relative t.
"""

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


def normalize_line(obj: dict) -> dict | None:
    """One raw events.jsonl line -> {"t","type","data"} or None if unusable."""
    if not isinstance(obj, dict):
        return None
    t = obj.get("t")
    data = obj.get("data")
    if not isinstance(data, dict):
        return None
    if t is None:
        t = data.get("ts")
    if not isinstance(t, (int, float)) or not isinstance(obj.get("type"), str):
        return None
    return {"t": float(t), "type": obj["type"], "data": data}


@dataclass
class _Entry:
    id: str
    base: Path
    video: Path
    events_file: Path
    meta: dict
    treats: list[float]


class DemoClips:
    def __init__(self, manifest: Path | None, bundled: Path) -> None:
        self._clips: dict[str, _Entry] = {}
        for src in ([manifest] if manifest and manifest.exists() else []) + [bundled / "manifest.json"]:
            try:
                doc = json.loads(Path(src).read_text())
            except (OSError, ValueError):
                continue
            if not isinstance(doc.get("clips"), list):
                continue
            for c in doc["clips"]:
                if not isinstance(c, dict):
                    continue
                entry = self._parse_entry(Path(src).parent, c)
                if entry is not None and entry.id not in self._clips:
                    self._clips[entry.id] = entry
            if self._clips:
                break

    @staticmethod
    def _parse_entry(base: Path, c: dict) -> _Entry | None:
        if "events" in c and "clip" in c:
            # Person A style.
            video, events = base / c["clip"], base / c["events"]
            if not (video.exists() and events.exists()):
                return None
            meta = {"name": c.get("name"), "emotion": c.get("expected_emotion", "unknown"),
                    "duration_s": c.get("duration_s", 0.0)}
            treats = [float(x) for x in (c.get("treats") or []) if isinstance(x, (int, float))]
            return _Entry(str(c.get("name", c["clip"])), base, video, events, meta, treats)
        if "id" in c and "dir" in c:
            # Web style.
            d = base / c["dir"]
            if not ((d / "clip.mp4").exists() and (d / "events.jsonl").exists()):
                return None
            try:
                meta = json.loads((d / "meta.json").read_text())
            except (OSError, ValueError):
                meta = {}
            meta = {"name": meta.get("name", c.get("name", c["id"])),
                    "emotion": meta.get("emotion", c.get("emotion", "unknown")),
                    "duration_s": meta.get("duration_s", 0.0)}
            return _Entry(str(c["id"]), d, d / "clip.mp4", d / "events.jsonl", meta, [])
        return None

    @property
    def available(self) -> bool:
        return bool(self._clips)

    def list(self) -> list[ClipMeta]:
        return [ClipMeta(e.id, str(e.meta.get("name", e.id)), str(e.meta.get("emotion", "unknown")),
                         float(e.meta.get("duration_s", 0.0)), (CACHE / f"{e.id}.timeline.json").exists())
                for e in self._clips.values()]

    def _entry(self, cid: str) -> _Entry:
        try:
            return self._clips[cid]
        except KeyError:
            raise KeyError(f"unknown demo clip {cid!r}") from None

    def video_path(self, cid: str) -> Path:
        return self._entry(cid).video

    def clip_dir(self, cid: str) -> Path:
        return self._entry(cid).video.parent

    def events(self, cid: str) -> list[dict]:
        """Normalized {"t","type","data"} events, sorted by t, with manifest treats injected."""
        e = self._entry(cid)
        out: list[dict] = []
        for line in e.events_file.read_text().splitlines():
            if not line.strip():
                continue
            try:
                obj = json.loads(line)
            except ValueError:
                continue
            norm = normalize_line(obj)
            if norm is not None:
                out.append(norm)
        have_treats = {x["t"] for x in out if x["type"] == "treat"}
        for tt in e.treats:
            if all(abs(tt - h) > 0.01 for h in have_treats):
                out.append({"t": tt, "type": "treat", "data": {"ts": tt}})
        out.sort(key=lambda x: x["t"])
        return out

    def timeline(self, cid: str) -> dict | None:
        self._entry(cid)
        p = CACHE / f"{cid}.timeline.json"
        if not p.exists():
            return None
        return json.loads(p.read_text())


def load_demo_clips(cfg: dict) -> DemoClips:
    demo = cfg["web"]["demo"]
    return DemoClips(Path(demo["manifest"]), Path(demo["clips_dir"]))
