import base64
import json

import cv2
import numpy as np

from backend.contracts import EMOTIONS, AudioEvent, Features, RulesLabel
from backend.fusion.prompts import SYSTEM_PROMPT, build_user_content, encode_frame


def test_system_prompt_lists_vocabulary_and_json_only():
    for e in EMOTIONS:
        assert e in SYSTEM_PROMPT
    assert "JSON" in SYSTEM_PROMPT and "unknown" in SYSTEM_PROMPT


def _inputs():
    feats = [(9.0, Features(tail_height=0.5, tail_wag_hz=3.2)), (10.0, Features(motion_energy=0.7))]
    audio = [AudioEvent(ts=9.5, label="yip", score=0.8)]
    rules = RulesLabel(ts=10.0, emotion="excited", confidence=0.7, scores={e: 0.1 for e in EMOTIONS})
    return feats, audio, rules


def test_text_only_when_no_image():
    feats, audio, rules = _inputs()
    c = build_user_content(feats, audio, rules, None, now=10.0)
    assert isinstance(c, str)
    payload = json.loads(c[c.index("{"):])
    assert payload["rules_label"] == {"emotion": "excited", "confidence": 0.7}
    assert payload["recent_sounds"][0]["label"] == "yip"
    assert payload["features"][0]["t_minus_s"] == 1.0
    assert "motion_energy" in payload["features"][1] and "ear_position" not in payload["features"][1]


def test_image_part_when_vision():
    feats, audio, rules = _inputs()
    c = build_user_content(feats, audio, rules, "QUJD", now=10.0)
    assert [p["type"] for p in c] == ["text", "image_url"]
    assert c[1]["image_url"]["url"] == "data:image/jpeg;base64,QUJD"


def test_encode_frame_downscales_long_side():
    ok, buf = cv2.imencode(".jpg", np.zeros((1280, 720, 3), np.uint8))
    out = base64.b64decode(encode_frame(buf.tobytes(), 512))
    img = cv2.imdecode(np.frombuffer(out, np.uint8), cv2.IMREAD_COLOR)
    assert max(img.shape[:2]) == 512 and img.shape[0] > img.shape[1]  # portrait kept
    assert encode_frame(b"junk", 512) is None
