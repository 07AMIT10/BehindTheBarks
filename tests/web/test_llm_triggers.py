from backend.contracts import EMOTIONS, AudioEvent, RulesLabel
from backend.fusion.llm_triggers import TriggerPolicy


def R(e, ts=0.0):
    return RulesLabel(ts=ts, emotion=e, confidence=0.5, scores={x: 0.1 for x in EMOTIONS})


def test_heartbeat_fires_first_then_every_10s():
    p = TriggerPolicy()
    assert p.due(0.0, False) == "heartbeat"
    assert p.due(5.0, False) is None
    assert p.due(10.0, False) == "heartbeat"


def test_min_interval_gates_everything():
    p = TriggerPolicy()
    p.due(0.0, False)
    p.note_treat()
    assert p.due(2.9, False) is None
    assert p.due(3.0, False) == "treat"


def test_priority_treat_over_audio_over_rules():
    p = TriggerPolicy()
    p.due(0.0, False)
    p.note_rules(R("happy")); p.note_rules(R("excited"))
    p.note_audio(AudioEvent(ts=1, label="bark", score=0.9))
    p.note_treat()
    assert p.due(3.0, False) == "treat"
    assert p.due(6.0, False) is None  # lower-priority triggers were overwritten, not queued


def test_audio_threshold_and_non_dog_labels():
    p = TriggerPolicy()
    p.due(0.0, False)
    p.note_audio(AudioEvent(ts=1, label="bark", score=0.6))      # not > 0.6
    p.note_audio(AudioEvent(ts=1, label="silence", score=0.99))  # not a dog sound
    assert p.due(3.0, False) is None
    p.note_audio(AudioEvent(ts=2, label="whimper", score=0.61))
    assert p.due(3.1, False) == "audio"


def test_rules_change_only_on_change():
    p = TriggerPolicy()
    p.due(0.0, False)
    p.note_rules(R("happy"))  # first label: no change event
    assert p.due(3.0, False) is None
    p.note_rules(R("happy"))
    assert p.due(4.0, False) is None
    p.note_rules(R("anxious"))
    assert p.due(5.0, False) == "rules_change"


def test_in_flight_defers_latest_wins():
    p = TriggerPolicy()
    p.due(0.0, False)
    p.note_audio(AudioEvent(ts=1, label="bark", score=0.9))
    assert p.due(4.0, True) is None      # call in flight: nothing fires, trigger kept
    assert p.due(4.5, False) == "audio"  # fires once flight is over
