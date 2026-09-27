import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from scripts import tune  # noqa: E402


def test_load_events_buckets_by_type(tmp_path):
    path = tmp_path / "clip.jsonl"
    path.write_text("\n".join(json.dumps(r) for r in [
        {"type": "frame", "data": {"ts": 0.0}},
        {"type": "audio", "data": {"ts": 0.5, "label": "bark", "score": 0.8}},
        {"type": "rules", "data": {"ts": 0.0, "emotion": "happy"}},
        {"type": "treat", "data": {"ts": 1.0}},
        {"type": "frame", "data": {"ts": 0.1}},
    ]) + "\n")
    events = tune.load_events(path)
    assert [len(events[k]) for k in ("frame", "audio", "rules", "treat")] == [2, 1, 1, 1]
    assert events["audio"][0]["label"] == "bark"


@pytest.mark.parametrize("seconds,duration,expected", [
    ({}, 10.0, "(no rules output)"),
    ({"happy": 5.0}, 0.0, "(no rules output)"),
    ({"relaxed": 7.0, "happy": 1.6, "disinterested": 1.4}, 10.0, "relaxed 70%, happy 16%, disinterested 14%"),
    ({"happy": 5.0, "relaxed": 0.0}, 10.0, "happy 50%"),  # zero shares are dropped
])
def test_label_breakdown(seconds, duration, expected):
    assert tune.label_breakdown(seconds, duration) == expected


def test_plot_clip_writes_a_png_and_returns_the_dominant_label(tmp_path):
    frames = [{"ts": t, "features": {"tail_height": 0.1, "tail_wag_hz": 1.0, "mouth_open": 0.5,
                                     "body_lowering": 0.1, "motion_energy": 0.3, "in_feeding_zone": True,
                                     "ear_position": "neutral"}}
             for t in (0.0, 1.0, 2.0)]
    rules = [{"ts": t, "emotion": "happy", "scores": {e: 0.1 for e in
             ("happy", "excited", "relaxed", "anxious", "fearful", "aggressive", "disinterested", "unknown")}}
            for t in (0.0, 1.0, 2.0)]
    rules[0]["scores"]["happy"] = 0.9
    events = {"frame": frames, "audio": [{"ts": 0.5, "label": "bark", "score": 0.8}],
             "rules": rules, "treat": [{"ts": 1.5}]}
    out = tmp_path / "clip.png"
    obs = tune.plot_clip("clip", {"duration_s": 2.0, "expected_emotion": "happy"}, events, out)
    assert out.is_file() and out.stat().st_size > 0
    assert obs["dominant"] == "happy"
