"""Copy the demo MP4s to R2 under demo/<clip id>.mp4. Idempotent: an object whose size already matches
the local file is skipped, so re-running after a clip change uploads only what changed.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from backend.demo.clips import DemoClips, demo_object_key


@dataclass(frozen=True)
class SyncResult:
    clip_id: str
    key: str
    action: str  # "uploaded" | "skipped" | "would-upload"
    size: int


def sync_demo_clips(r2: Any, clips: DemoClips, *, dry_run: bool = False) -> list[SyncResult]:
    results: list[SyncResult] = []
    for meta in clips.list():
        path = clips.video_path(meta.id)
        key = demo_object_key(meta.id)
        size = path.stat().st_size
        if r2.object_size(key) == size:
            results.append(SyncResult(meta.id, key, "skipped", size))
        elif dry_run:
            results.append(SyncResult(meta.id, key, "would-upload", size))
        else:
            r2.put_file(path, key, content_type="video/mp4")
            results.append(SyncResult(meta.id, key, "uploaded", size))
    return results
