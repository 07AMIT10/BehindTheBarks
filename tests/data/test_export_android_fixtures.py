from __future__ import annotations

import json
from pathlib import Path
import numpy as np
import pytest

from backend.contracts import EMOTIONS, AudioEvent, Features, RulesLabel
from backend.vision.keypoint_map import CANONICAL_NAMES
from scripts.export_android_fixtures import (
    export_logic_fixtures,
    export_model_io_fixtures,
    export_audio_fixtures,
    main as export_main,
)

FIXTURES_DIR = Path("tests/android/fixtures")


def test_logic_fixture_structure(tmp_path: Path):
    out_dir = tmp_path / "logic"
    # Export 10 frames from waiting_corgi
    clips = ["waiting_corgi"]
    export_logic_fixtures(out_dir=out_dir, clips=clips, limit_frames=10)

    fixture_file = out_dir / "waiting_corgi.jsonl"
    assert fixture_file.is_file()
    assert fixture_file.stat().st_size < 1_000_000  # must stay under 1 MB

    records = []
    with fixture_file.open() as f:
        for line in f:
            records.append(json.loads(line))

    assert len(records) == 10
    prev_ts = -1.0
    for r in records:
        assert "ts" in r
        assert r["ts"] >= prev_ts
        prev_ts = r["ts"]

        # Assert all kinds specified in the plan
        for k in ("det", "keypoints", "face", "wag_roi", "features", "rules"):
            assert k in r

        # Features validate against contract
        feat = Features.model_validate(r["features"])
        assert isinstance(feat.in_feeding_zone, bool)

        # Rules validate against contract
        rules = RulesLabel.model_validate(r["rules"])
        assert rules.emotion in EMOTIONS
        assert 0.0 <= rules.confidence <= 1.0


def test_model_io_fixture_structure(tmp_path: Path):
    out_dir = tmp_path / "models"
    export_model_io_fixtures(out_dir=out_dir, n_samples=2)

    for model_name in ("detector", "pose", "face", "audio"):
        model_dir = out_dir / model_name
        assert model_dir.is_dir()
        for idx in range(2):
            inp_bin = model_dir / f"input_{idx}.bin"
            inp_json = model_dir / f"input_{idx}_shape.json"
            out_bin = model_dir / f"output_{idx}.bin"
            out_json = model_dir / f"output_{idx}_shape.json"

            assert inp_bin.is_file()
            assert inp_json.is_file()
            assert out_bin.is_file()
            assert out_json.is_file()

            meta_in = json.loads(inp_json.read_text())
            assert "shape" in meta_in
            assert "dtype" in meta_in
            expected_in_bytes = int(np.prod(meta_in["shape"]) * np.dtype(meta_in["dtype"]).itemsize)
            assert inp_bin.stat().st_size == expected_in_bytes

            meta_out = json.loads(out_json.read_text())
            assert "shape" in meta_out
            assert "dtype" in meta_out


def test_audio_fixture_structure(tmp_path: Path):
    out_dir = tmp_path / "audio"
    export_audio_fixtures(out_dir=out_dir, duration_s=5.0)

    pcm_file = out_dir / "audio_5s_16k.pcm"
    assert pcm_file.is_file()
    # 5 seconds at 16,000 samples/sec * 2 bytes/sample = 160,000 bytes
    assert pcm_file.stat().st_size == 160_000

    windows_file = out_dir / "audio_windows.json"
    assert windows_file.is_file()
    windows = json.loads(windows_file.read_text())
    assert len(windows) > 0
    for w in windows:
        assert "ts" in w
        assert "scores" in w
        assert len(w["scores"]) == 521

    events_file = out_dir / "audio_events.json"
    assert events_file.is_file()
    events = json.loads(events_file.read_text())
    assert isinstance(events, list)
    for ev_data in events:
        AudioEvent.model_validate(ev_data)
