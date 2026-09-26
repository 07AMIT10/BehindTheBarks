import json

import pytest
from pydantic import ValidationError

from backend.contracts import AudioEvent, FrameEvent, Features, RulesLabel, EMOTIONS

FRAME = {
    "ts": 1727340000.123,
    "source": "live",
    "dog_detected": True,
    "bbox": [10, 20, 300, 400],
    "bbox_conf": 0.91,
    "body_keypoints": {"tail_base": [100, 200, 0.9], "tail_tip": [120, 210, 0.8]},
    "face_landmarks": [[1, 2], [3, 4]],
    "features": {
        "tail_height": 0.4,
        "tail_wag_hz": 2.1,
        "ear_position": "up",
        "mouth_open": 0.6,
        "body_lowering": 0.1,
        "motion_energy": 0.7,
        "in_feeding_zone": True,
    },
}


def test_vocabulary_is_fixed():
    assert set(EMOTIONS) == {
        "happy", "excited", "relaxed", "anxious", "fearful", "aggressive", "disinterested", "unknown",
    }


def test_frame_event_round_trip():
    ev = FrameEvent.model_validate(FRAME)
    again = FrameEvent.model_validate_json(ev.to_json())
    assert again == ev
    assert json.loads(ev.to_json())["features"]["ear_position"] == "up"


def test_audio_event_round_trip():
    ev = AudioEvent(ts=1727340000.5, label="bark", score=0.82)
    assert AudioEvent.model_validate_json(ev.to_json()) == ev


def test_rules_label_round_trip():
    ev = RulesLabel(ts=1.0, emotion="excited", confidence=0.7, scores={"excited": 0.7, "happy": 0.2})
    assert RulesLabel.model_validate_json(ev.to_json()) == ev


def test_no_dog_frame_with_null_everything():
    ev = FrameEvent(ts=1.0, source="live", dog_detected=False)
    assert ev.bbox is None and ev.face_landmarks is None
    assert all(v is None for v in ev.features.model_dump().values())
    assert FrameEvent.model_validate_json(ev.to_json()) == ev


def test_null_features_accepted():
    data = {**FRAME, "features": {k: None for k in FRAME["features"]}}
    ev = FrameEvent.model_validate(data)
    assert ev.features.tail_height is None


def test_null_keypoint_accepted():
    data = {**FRAME, "body_keypoints": {"tail_tip": None}}
    assert FrameEvent.model_validate(data).body_keypoints["tail_tip"] is None


@pytest.mark.parametrize(
    "field,value",
    [
        ("tail_height", 1.1),
        ("tail_height", -1.1),
        ("mouth_open", 1.5),
        ("mouth_open", -0.1),
        ("body_lowering", 2),
        ("motion_energy", -0.5),
        ("tail_wag_hz", -1),
        ("ear_position", "flat"),
    ],
)
def test_out_of_range_features_rejected(field, value):
    with pytest.raises(ValidationError):
        Features(**{field: value})


def test_tail_height_bounds_inclusive():
    Features(tail_height=-1.0)
    Features(tail_height=1.0)


def test_bad_frame_fields_rejected():
    with pytest.raises(ValidationError):
        FrameEvent.model_validate({**FRAME, "bbox_conf": 1.2})
    with pytest.raises(ValidationError):
        FrameEvent.model_validate({**FRAME, "body_keypoints": {"tail_tip": [1, 2, 1.5]}})
    with pytest.raises(ValidationError):
        FrameEvent.model_validate({**FRAME, "surprise": 1})


def test_audio_rejects_bad_label_and_score():
    with pytest.raises(ValidationError):
        AudioEvent(ts=1, label="meow", score=0.5)
    with pytest.raises(ValidationError):
        AudioEvent(ts=1, label="bark", score=1.2)


def test_rules_label_rejects_bad_emotion_and_scores():
    with pytest.raises(ValidationError):
        RulesLabel(ts=1, emotion="sad", confidence=0.5, scores={})
    with pytest.raises(ValidationError):
        RulesLabel(ts=1, emotion="happy", confidence=1.5, scores={})
    with pytest.raises(ValidationError):
        RulesLabel(ts=1, emotion="happy", confidence=0.5, scores={"happy": 2.0})
    with pytest.raises(ValidationError):
        RulesLabel(ts=1, emotion="happy", confidence=0.5, scores={"sad": 0.5})
