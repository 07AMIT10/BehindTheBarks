import json

from fastapi.testclient import TestClient

from backend.demo.clips import DemoClips, load_demo_clips
from backend.demo.mock_pipeline import MockPipeline
from backend.main import create_app
from backend.notify.dashboard import DashboardOnlyNotifier
from backend.web.settings import load_config


class NoLLM:
    enabled = False
    last_call = None


def _write_clip(root, cid="c1", n_events=4):
    d = root / cid
    d.mkdir(parents=True)
    (d / "clip.mp4").write_bytes(b"\x00\x00\x00\x18ftypmp42")
    evs = [{"t": i * 0.5, "type": "audio", "data": {"ts": i * 0.5, "label": "bark", "score": 0.9}} for i in range(n_events)]
    (d / "events.jsonl").write_text("\n".join(json.dumps(e) for e in evs) + "\n")
    (d / "meta.json").write_text(json.dumps({"name": "Barks", "emotion": "excited", "duration_s": 2.0}))
    (root / "manifest.json").write_text(json.dumps({"clips": [{"id": cid, "name": "Barks", "emotion": "excited", "dir": cid}]}))
    return root / "manifest.json"


def test_clips_loader_and_fallback(tmp_path):
    man = _write_clip(tmp_path / "a")
    clips = DemoClips(man, tmp_path / "bundled-missing")
    assert [c.id for c in clips.list()] == ["c1"] and clips.available
    assert clips.list()[0].has_timeline is False
    assert len(clips.events("c1")) == 4 and clips.timeline("c1") is None
    assert clips.video_path("c1").name == "clip.mp4"
    try:
        clips.video_path("nope")
        assert False
    except KeyError:
        pass
    fallback = DemoClips(tmp_path / "no-manifest.json", tmp_path / "b")
    (tmp_path / "b").mkdir()
    (tmp_path / "b" / "manifest.json").write_text(json.dumps({"clips": []}))
    assert fallback.available is False and fallback.list() == []


def test_load_demo_clips_prefers_manifest(tmp_path):
    man = _write_clip(tmp_path / "m")
    cfg = load_config(tmp_path / "none.yaml", env={})
    cfg["web"]["demo"]["manifest"] = str(man)
    assert [c.id for c in load_demo_clips(cfg).list()] == ["c1"]


def _client(tmp_path):
    man = _write_clip(tmp_path / "clips")
    cfg = load_config(tmp_path / "none.yaml", env={})
    cfg["web"]["demo"]["manifest"] = str(man)
    cfg["web"]["log_dir"] = str(tmp_path / "out")
    app = create_app(cfg, pipeline=MockPipeline(cfg), interpreter=NoLLM(), notifier=DashboardOnlyNotifier())
    return TestClient(app)


def test_demo_endpoints_and_mode_switch(tmp_path):
    with _client(tmp_path) as c:
        assert c.get("/status").json()["modes"] == ["live", "demo"]
        clips = c.get("/demo/clips").json()
        assert clips[0]["id"] == "c1" and clips[0]["duration_s"] == 2.0
        ev = c.get("/demo/clips/c1/events").json()
        assert ev["events"][0]["type"] == "audio"
        assert c.get("/demo/clips/c1/timeline").status_code == 404
        v = c.get("/demo/clips/c1/video")
        assert v.status_code == 200 and v.content.startswith(b"\x00\x00\x00\x18ftypmp42")
        r = c.get("/demo/clips/c1/video", headers={"Range": "bytes=0-7"})
        assert r.status_code == 206 and r.content == b"\x00\x00\x00\x18ftyp"
        assert c.post("/mode", json={"mode": "demo"}).json() == {"mode": "demo"}
        st = c.get("/status").json()
        assert st["mode"] == "demo"
        assert c.post("/mode", json={"mode": "live"}).json() == {"mode": "live"}
        assert c.post("/mode", json={"mode": "x"}).status_code == 400


def test_demo_notify_once_and_disabled(tmp_path):
    with _client(tmp_path) as c:
        r1 = c.post("/demo/notify", json={"clip": "c1", "t": 1.0}).json()
        assert r1["status"] == "dashboard_only"  # default: telegram_first false
        r2 = c.post("/demo/notify", json={"clip": "c1", "t": 2.0}).json()
        assert r2["duplicate"] is True


def test_clips_loader_person_a_schema(tmp_path):
    from backend.demo.clips import DemoClips
    base = tmp_path / "pack"
    (base / "clips").mkdir(parents=True)
    (base / "events").mkdir(parents=True)
    (base / "clips" / "x.mp4").write_bytes(b"v")
    (base / "events" / "x.jsonl").write_text(
        '{"type": "frame", "data": {"ts": 0.0, "source": "file", "dog_detected": false}}\n'
        '{"type": "rules", "data": {"ts": 0.0, "emotion": "unknown", "confidence": 1.0, '
        '"scores": {"unknown": 1.0}}}\n')
    (base / "manifest.json").write_text(json.dumps({
        "version": 1, "ts_origin": "clip_start",
        "clips": [{"name": "x", "expected_emotion": "relaxed", "treats": [1.5], "notes": "",
                   "clip": "clips/x.mp4", "duration_s": 2.0, "events": "events/x.jsonl"}]}))
    clips = DemoClips(base / "manifest.json", tmp_path / "bundled-missing")
    assert clips.available
    meta = clips.list()[0]
    assert (meta.id, meta.name, meta.emotion, meta.duration_s) == ("x", "x", "relaxed", 2.0)
    evs = clips.events("x")
    assert [e["t"] for e in evs] == [0.0, 0.0, 1.5]
    assert evs[-1]["type"] == "treat"
