import pytest

from backend.contracts import EMOTIONS, AudioEvent, Features, FrameEvent
from backend.fusion.rules import RulesEngine

CFG = {"data": {"rules": {}}}
FPS = 8

# Synthetic feature windows, one per emotion (values a real dog of that mood would plausibly give).
EXCITED = dict(tail_height=0.7, tail_wag_hz=4.5, ear_position="up", mouth_open=0.5, body_lowering=0.0, motion_energy=0.9, in_feeding_zone=True)
HAPPY = dict(tail_height=0.4, tail_wag_hz=2.0, ear_position="neutral", mouth_open=0.6, body_lowering=0.05, motion_energy=0.4, in_feeding_zone=True)
RELAXED = dict(tail_height=0.05, tail_wag_hz=0.0, ear_position="neutral", mouth_open=0.1, body_lowering=0.0, motion_energy=0.05, in_feeding_zone=True)
ANXIOUS = dict(tail_height=-0.45, tail_wag_hz=0.0, ear_position="back", mouth_open=0.1, body_lowering=0.3, motion_energy=0.5, in_feeding_zone=False)
FEARFUL = dict(tail_height=-0.9, tail_wag_hz=0.0, ear_position="back", mouth_open=0.0, body_lowering=0.8, motion_energy=0.1, in_feeding_zone=False)
AGGRESSIVE = dict(tail_height=0.5, tail_wag_hz=0.0, ear_position="up", mouth_open=0.5, body_lowering=0.0, motion_energy=0.05, in_feeding_zone=True)


def frame(ts, dog=True, **features):
    return FrameEvent(ts=ts, source="test", dog_detected=dog, features=Features(**features))


def audio(ts, label, score=0.9):
    return AudioEvent(ts=ts, label=label, score=score)


def run(engine, feats, seconds, t0=0.0, sounds=(), treat=False):
    """Feed constant features at FPS; return the list of RulesLabels."""
    return [engine.update(frame(t0 + i / FPS, **feats), sounds, treat) for i in range(int(seconds * FPS))]


def make(**over):
    return RulesEngine({"data": {"rules": over}})


# -- each emotion ----------------------------------------------------------------------------


@pytest.mark.parametrize(
    "feats, sounds, want",
    [
        (EXCITED, [audio(0.5, "yip")], "excited"),
        (HAPPY, [], "happy"),
        (RELAXED, [], "relaxed"),
        (ANXIOUS, [audio(0.5, "whimper")], "anxious"),
        (FEARFUL, [audio(0.5, "whimper")], "fearful"),
        (AGGRESSIVE, [audio(0.5, "growl")], "aggressive"),
    ],
)
def test_synthetic_window_gives_its_emotion(feats, sounds, want):
    label = run(make(), feats, 3.0, sounds=sounds)[-1]
    assert label.emotion == want
    assert label.confidence > 0.4
    assert label.scores[want] == max(label.scores.values())


def test_excited_and_anxious_hold_up_without_any_sound():
    assert run(make(), EXCITED, 3.0)[-1].emotion == "excited"
    assert run(make(), ANXIOUS, 3.0)[-1].emotion == "anxious"


def test_every_label_has_a_score_for_every_emotion_in_range():
    label = run(make(), HAPPY, 1.0)[-1]
    assert set(label.scores) == set(EMOTIONS)
    assert all(0.0 <= s <= 1.0 for s in label.scores.values())
    assert 0.0 <= label.confidence <= 1.0


# -- sounds and treats -----------------------------------------------------------------------


def test_growl_wipes_out_happy_and_relaxed():
    for feats in (HAPPY, RELAXED):
        label = run(make(), feats, 2.0, sounds=[audio(1.0, "growl", 1.0)])[-1]
        assert label.scores["happy"] == label.scores["relaxed"] == 0.0


def test_aggressive_is_capped_without_a_growl_and_uncapped_with_one():
    plain = run(make(), AGGRESSIVE, 3.0)[-1]
    assert plain.scores["aggressive"] <= 0.5
    assert plain.emotion != "aggressive"
    growly = run(make(), AGGRESSIVE, 3.0, sounds=[audio(0.5, "growl")])[-1]
    assert growly.scores["aggressive"] > 0.5
    assert growly.emotion == "aggressive"


def test_a_weak_growl_does_not_lift_the_cap():
    label = run(make(), AGGRESSIVE, 3.0, sounds=[audio(0.5, "growl", 0.1)])[-1]
    assert label.scores["aggressive"] <= 0.5


def test_treat_event_boosts_excited():
    feats = {**EXCITED, "tail_wag_hz": 2.0, "motion_energy": 0.5}  # ambiguous on its own
    without = run(make(), feats, 1.0)[-1].scores["excited"]
    with_treat = run(make(), feats, 1.0, treat=True)[-1].scores["excited"]
    assert with_treat > without


def test_old_audio_events_are_ignored():
    label = make().update(frame(10.0, **RELAXED), [audio(5.0, "growl", 1.0)])  # 5 s old, window is 3 s
    assert label.scores["relaxed"] > 0.8


# -- null features ---------------------------------------------------------------------------


def test_null_features_are_not_evidence_either_way():
    full = run(make(), EXCITED, 2.0)[-1]
    no_ears = run(make(), {**EXCITED, "ear_position": None}, 2.0)[-1]
    assert no_ears.emotion == "excited"
    assert no_ears.scores["excited"] >= 0.9
    assert abs(no_ears.scores["excited"] - full.scores["excited"]) < 0.1


def test_unknown_ear_position_counts_as_null():
    label = run(make(), {**EXCITED, "ear_position": "unknown"}, 2.0)[-1]
    assert label.scores["excited"] >= 0.9


def test_all_null_features_with_a_dog_is_unknown():
    label = run(make(), {}, 2.0)[-1]
    assert label.emotion == "unknown"
    assert all(s == 0.0 for e, s in label.scores.items() if e != "unknown")


def test_one_lonely_feature_cannot_max_out_an_emotion():
    label = run(make(), {"tail_height": 0.9}, 2.0)[-1]
    assert max(s for e, s in label.scores.items() if e != "unknown") < 0.6


# -- disinterested ---------------------------------------------------------------------------


IDLE = dict(RELAXED, motion_energy=0.02)


def test_disinterested_needs_the_full_duration():
    labels = run(make(), IDLE, 12.0)
    assert labels[int(6 * FPS)].emotion == "relaxed"  # 6 s idle: not yet
    assert labels[-1].emotion == "disinterested"
    assert labels[-1].scores["disinterested"] >= 0.7


def test_idle_outside_the_zone_is_not_disinterested():
    label = run(make(), {**IDLE, "in_feeding_zone": False}, 12.0)[-1]
    assert label.scores["disinterested"] == 0.0


def test_a_treat_restarts_the_idle_clock():
    engine = make()
    run(engine, IDLE, 9.0)  # would be disinterested by now
    labels = run(engine, IDLE, 5.0, t0=9.0, treat=True)
    assert all(l.scores["disinterested"] == 0.0 for l in labels)
    assert run(engine, IDLE, 5.0, t0=14.0)[-1].scores["disinterested"] == 0.0  # clock restarted at 14 s


def test_a_brief_motion_blip_does_not_restart_the_idle_clock():
    engine = make()
    run(engine, IDLE, 6.0)
    run(engine, {**IDLE, "motion_energy": 0.4}, 0.5, t0=6.0)  # shorter than the grace period
    assert run(engine, IDLE, 4.0, t0=6.5)[-1].scores["disinterested"] > 0.0


def test_real_motion_restarts_the_idle_clock():
    engine = make()
    run(engine, IDLE, 9.0)
    run(engine, {**IDLE, "motion_energy": 0.5}, 3.0, t0=9.0)
    assert run(engine, IDLE, 5.0, t0=12.0)[-1].scores["disinterested"] == 0.0


# -- no dog ----------------------------------------------------------------------------------


def test_no_dog_for_over_two_seconds_is_unknown_but_a_short_gap_holds_the_label():
    engine = make()
    run(engine, EXCITED, 3.0)
    held = engine.update(frame(3.0 + 1.0, dog=False))
    assert held.emotion == "excited"
    assert held.ts == 4.0
    gone = engine.update(frame(3.0 + 2.5, dog=False))
    assert gone.emotion == "unknown"
    assert gone.scores["unknown"] == 1.0
    assert gone.confidence == 1.0


def test_no_dog_from_the_start_is_unknown():
    assert make().update(frame(0.0, dog=False)).emotion == "unknown"


def test_unknown_lifts_after_hysteresis_when_the_dog_returns():
    engine = make()
    engine.update(frame(0.0, dog=False))
    labels = run(engine, EXCITED, 3.0, t0=5.0)
    assert labels[0].emotion == "unknown"  # hysteresis: not yet
    assert labels[-1].emotion == "excited"


# -- hysteresis ------------------------------------------------------------------------------


def test_flicker_input_stays_stable():
    engine = make()
    run(engine, RELAXED, 2.0)
    labels = []
    for i in range(4 * FPS):  # relaxed <-> excited every frame
        feats = EXCITED if i % 2 else RELAXED
        labels.append(engine.update(frame(2.0 + i / FPS, **feats)))
    assert {l.emotion for l in labels} == {"relaxed"}


def test_a_sustained_change_switches_after_about_hysteresis_s():
    engine = make()
    run(engine, RELAXED, 2.0)
    labels = run(engine, EXCITED, 3.0, t0=2.0)
    switched = next(l.ts for l in labels if l.emotion == "excited")
    assert 2.0 + 1.5 <= switched <= 2.0 + 1.5 + 1.0 / FPS
    assert labels[0].emotion == "relaxed"


def test_zero_hysteresis_switches_immediately():
    engine = make(hysteresis_s=0.0)
    run(engine, RELAXED, 1.0)
    assert engine.update(frame(1.0, **EXCITED)).emotion == "excited"


def test_first_frame_needs_no_hysteresis():
    assert make().update(frame(0.0, **EXCITED)).emotion == "excited"


def test_held_label_reports_its_own_lower_confidence():
    engine = make()
    run(engine, RELAXED, 2.0)
    held = engine.update(frame(2.0, **EXCITED))  # excited leads on the raw score, relaxed is still held
    assert held.emotion == "relaxed"
    assert held.confidence < 0.3


# -- config ----------------------------------------------------------------------------------


def test_config_overrides_reach_the_scoring():
    strict = make(disinterested_s=2.0)
    assert run(strict, IDLE, 4.0)[-1].emotion == "disinterested"
    weak = {"tail_height": 0.9}  # one lonely feature: real but thin evidence
    assert run(make(min_score=0.99), weak, 2.0)[-1].emotion == "unknown"
    assert run(make(min_score=0.05), weak, 2.0)[-1].emotion != "unknown"


def test_reset_forgets_history():
    engine = make()
    run(engine, IDLE, 12.0)
    engine.reset()
    assert engine.update(frame(0.0, **RELAXED)).scores["disinterested"] == 0.0
