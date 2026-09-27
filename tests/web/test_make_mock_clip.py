import json
import shutil
import subprocess
import sys
from pathlib import Path

import cv2

ROOT = Path(__file__).resolve().parents[2]


def test_generates_playable_clip_and_valid_events(tmp_path):
    out = tmp_path / "mock"
    r = subprocess.run([sys.executable, "scripts/make_mock_clip.py", "--out", str(out),
                        "--duration", "4", "--fps", "4", "--width", "320", "--height", "240"],
                       cwd=ROOT, capture_output=True, text=True, timeout=300)
    assert r.returncode == 0, r.stderr
    assert (out / "clip.mp4").stat().st_size < 1_000_000
    cap = cv2.VideoCapture(str(out / "clip.mp4"))
    assert int(cap.get(cv2.CAP_PROP_FRAME_COUNT)) == 16
    ok, frame = cap.read()
    assert ok and frame.shape[:2] == (240, 320)
    meta = json.loads((out / "meta.json").read_text())
    assert meta["duration_s"] == 4 and meta["emotion"] == "excited"
    sys.path.insert(0, str(ROOT / "scripts"))
    from validate_clip import validate_clip
    man = tmp_path / "m.json"
    man.write_text(json.dumps({"clips": [{"id": "t", "name": "t", "emotion": "excited", "dir": "t"}]}))
    shutil.move(str(out), str(tmp_path / "t"))
    assert validate_clip(man) == []
