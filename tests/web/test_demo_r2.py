import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from backend.demo.clips import DemoClips, demo_object_key
from backend.demo.mock_pipeline import MockPipeline
from backend.demo.sync import sync_demo_clips
from backend.main import create_app
from backend.notify.dashboard import DashboardOnlyNotifier
from backend.web.settings import load_config


class NoLLM:
    enabled = False
    last_call = None


class FakeR2:
    """Stands in for R2SnapshotStorage: records uploads, presigns with a recognisable URL."""

    def __init__(self, remote_sizes: dict[str, int] | None = None):
        self.remote = dict(remote_sizes or {})
        self.puts: list[tuple[str, str]] = []

    def object_size(self, key):
        return self.remote.get(key)

    def put_file(self, path, key, content_type="video/mp4"):
        self.puts.append((str(path), key))
        self.remote[key] = Path(path).stat().st_size

    def url_for(self, key):
        return f"https://r2.example/presigned/{key}?sig=x"


def _demo(root: Path) -> Path:
    """Two clips, both named clip.mp4 on disk (as the bundled mock clips are), told apart by id."""
    for cid in ("alpha", "beta"):
        d = root / cid
        d.mkdir(parents=True)
        (d / "clip.mp4").write_bytes(b"\x00\x00\x00\x18ftypmp42" + cid.encode() * 10)
        (d / "events.jsonl").write_text(json.dumps({"t": 0.0, "type": "audio", "data": {"ts": 0.0, "label": "bark", "score": 0.9}}) + "\n")
        (d / "meta.json").write_text(json.dumps({"name": cid, "emotion": "excited", "duration_s": 2.0}))
    man = root / "manifest.json"
    man.write_text(json.dumps({"clips": [{"id": c, "name": c, "emotion": "excited", "dir": c} for c in ("alpha", "beta")]}))
    return man


def _app(tmp_path, monkeypatch, *, source: str, r2=None):
    man = _demo(tmp_path / "demo")
    cfg = load_config(tmp_path / "none.yaml", env={})
    cfg["web"]["log_dir"] = str(tmp_path / "out")
    cfg["web"]["demo"]["manifest"] = str(man)
    cfg["web"]["demo"]["clips_dir"] = str(tmp_path / "demo")
    cfg["web"]["demo"]["video_source"] = source
    if r2 is not None:
        monkeypatch.setattr("backend.main.build_r2", lambda _cfg: r2)
    return create_app(cfg, pipeline=MockPipeline(cfg), interpreter=NoLLM(), notifier=DashboardOnlyNotifier())


# -- sync -----------------------------------------------------------------------------------------

def test_sync_uploads_every_clip_under_its_id(tmp_path):
    man = _demo(tmp_path / "demo")
    clips = DemoClips(man, tmp_path / "demo")
    r2 = FakeR2()
    results = sync_demo_clips(r2, clips)
    assert {r.key for r in results} == {"demo/alpha.mp4", "demo/beta.mp4"}
    assert all(r.action == "uploaded" for r in results)
    assert len(r2.puts) == 2


def test_sync_skips_unchanged_clips_and_dry_run_uploads_nothing(tmp_path):
    man = _demo(tmp_path / "demo")
    clips = DemoClips(man, tmp_path / "demo")
    size = clips.video_path("alpha").stat().st_size
    r2 = FakeR2({"demo/alpha.mp4": size})
    dry = sync_demo_clips(r2, clips, dry_run=True)
    assert {r.clip_id: r.action for r in dry} == {"alpha": "skipped", "beta": "would-upload"}
    assert r2.puts == []
    real = sync_demo_clips(r2, clips)
    assert {r.clip_id: r.action for r in real} == {"alpha": "skipped", "beta": "uploaded"}


# -- serving --------------------------------------------------------------------------------------

def test_local_source_serves_the_file_itself(tmp_path, monkeypatch):
    app = _app(tmp_path, monkeypatch, source="local")
    with TestClient(app) as c:
        r = c.get("/demo/clips/alpha/video", follow_redirects=False)
        assert r.status_code == 200 and r.headers["content-type"] == "video/mp4"
        assert r.content.startswith(b"\x00\x00\x00\x18ftyp")


def test_r2_source_redirects_to_the_clip_object_by_id(tmp_path, monkeypatch):
    app = _app(tmp_path, monkeypatch, source="r2", r2=FakeR2())
    with TestClient(app) as c:
        r = c.get("/demo/clips/beta/video", follow_redirects=False)
        assert r.status_code == 302
        assert r.headers["location"] == f"https://r2.example/presigned/{demo_object_key('beta')}?sig=x"
        assert c.get("/demo/clips/nope/video", follow_redirects=False).status_code == 404


def test_r2_source_without_credentials_refuses_to_start(tmp_path, monkeypatch):
    for var in ("BTB_R2_ACCOUNT_ID", "R2_ACCOUNT_ID", "BTB_R2_ACCESS_KEY_ID", "R2_ACCESS_KEY_ID",
                "BTB_R2_SECRET_ACCESS_KEY", "R2_SECRET_ACCESS_KEY", "BTB_R2_BUCKET", "R2_BUCKET"):
        monkeypatch.delenv(var, raising=False)
    with pytest.raises(ValueError, match="R2 is not configured"):
        _app(tmp_path, monkeypatch, source="r2")


def test_unknown_video_source_refuses_to_start(tmp_path, monkeypatch):
    with pytest.raises(ValueError, match="must be 'local' or 'r2'"):
        _app(tmp_path, monkeypatch, source="s3")
