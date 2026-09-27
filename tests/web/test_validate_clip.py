import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))

from validate_clip import validate_clip


def _clip(tmp_path: Path, line: dict | str, meta: dict | None = None) -> Path:
    d = tmp_path / "c1"
    d.mkdir(parents=True)
    (d / "clip.mp4").write_bytes(b"fake")
    body = line if isinstance(line, str) else json.dumps(line)
    (d / "events.jsonl").write_text(body + "\n")
    (d / "meta.json").write_text(json.dumps(meta or {"name": "C", "emotion": "happy", "duration_s": 1.0}))
    man = tmp_path / "manifest.json"
    man.write_text(json.dumps({"clips": [{"id": "c1", "name": "C", "emotion": "happy", "dir": "c1"}]}))
    return man


def test_valid_clip(tmp_path):
    man = _clip(tmp_path, {"t": 0.5, "type": "audio", "data": {"ts": 0.5, "label": "bark", "score": 0.9}})
    assert validate_clip(man) == []


def test_bad_lines_reported(tmp_path):
    man = _clip(tmp_path, '{"t": 1, "type": "audio", "data": {"ts": 1, "label": "moo", "score": 2}}')
    errs = validate_clip(man)
    assert len(errs) == 1 and "c1" in errs[0] and "line 1" in errs[0]


def test_missing_files_and_manifest_problems(tmp_path):
    man = tmp_path / "manifest.json"
    man.write_text(json.dumps({"clips": [{"id": "x", "name": "X", "emotion": "sad", "dir": "x"}]}))
    errs = validate_clip(man)
    assert any("manifest" in e and "emotion" in e for e in errs)
    assert any("clip.mp4" in e for e in errs)


def test_demo_defaults(tmp_path):
    from backend.web.settings import load_config
    cfg = load_config(tmp_path / "none.yaml", env={})
    assert cfg["web"]["demo"] == {"manifest": "data/fallback/manifest.json", "clips_dir": "backend/demo/clips",
                                  "telegram_first": False}
