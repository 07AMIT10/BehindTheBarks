import pytest

from backend.contracts import EMOTIONS, FrameEvent, LLMResult, RulesLabel
from backend.fusion.state import FusionState, StateConfig, combine, rules_reason

CFG = StateConfig()


def R(emotion, conf=0.6, ts=0.0, second="happy", second_score=0.3):
    scores = {e: 0.0 for e in EMOTIONS}
    scores[second] = second_score
    scores[emotion] = conf
    return RulesLabel(ts=ts, emotion=emotion, confidence=conf, scores=scores)


def L(emotion, conf=0.8, ts=0.0):
    return LLMResult(ts=ts, emotion=emotion, confidence=conf, reason="LLM says so.",
                     provider="p", model="m", latency_ms=100.0, trigger="heartbeat")


def dog(ts, seen=True):
    return FrameEvent(ts=ts, source="t", dog_detected=seen)


# ---- combine() -------------------------------------------------------------------------------
def test_agree_is_fused_with_mean_confidence():
    s = combine(R("happy", 0.6), L("happy", 0.8), now=1, dog_absent_s=0, cfg=CFG)
    assert (s.emotion, s.source, s.reason) == ("happy", "fused", "LLM says so.")
    assert s.confidence == pytest.approx(0.7)


def test_disagree_llm_wins_at_threshold():
    s = combine(R("relaxed"), L("excited", 0.7), now=1, dog_absent_s=0, cfg=CFG)
    assert (s.emotion, s.source, s.confidence) == ("excited", "llm", 0.7)


def test_disagree_rules_win_below_threshold():
    s = combine(R("relaxed", 0.55), L("excited", 0.69), now=1, dog_absent_s=0, cfg=CFG)
    assert (s.emotion, s.source, s.confidence) == ("relaxed", "rules", 0.55)
    assert s.reason.startswith("Rules: relaxed 0.55")


def test_no_dog_beyond_threshold_is_unknown_even_if_llm_confident():
    s = combine(R("happy"), L("happy", 0.95), now=5, dog_absent_s=2.01, cfg=CFG)
    assert (s.emotion, s.confidence) == ("unknown", 1.0)


def test_nothing_yet_is_unknown_zero_conf():
    s = combine(None, None, now=0, dog_absent_s=0, cfg=CFG)
    assert (s.emotion, s.confidence, s.reason) == ("unknown", 0.0, "Waiting for data.")


def test_llm_only():
    assert combine(None, L("anxious"), now=1, dog_absent_s=0, cfg=CFG).source == "llm"


def test_rules_reason_top_two():
    assert rules_reason(R("excited", 0.82, second="happy", second_score=0.4)) == "Rules: excited 0.82, happy 0.40"


# ---- FusionState -----------------------------------------------------------------------------
def run(fs, t0, t1, rules=None, llm=None, dt=0.25, seen=True):
    ticks, t = [], t0
    while t <= t1 + 1e-9:
        fs.on_frame(dog(t, seen))
        if rules:
            fs.on_rules(R(rules, ts=t))
        if llm:
            fs.on_llm(L(llm, ts=t))
        ticks.append(fs.tick(t))
        t = round(t + dt, 6)
    return ticks


def test_first_state_adopted_immediately_without_notify():
    fs = FusionState(CFG)
    t = run(fs, 0, 0, rules="relaxed")[0]
    assert t.changed.emotion == "relaxed" and t.notify is None


def test_flicker_does_not_change_state():
    fs = FusionState(CFG)
    run(fs, 0, 1, rules="relaxed")
    ticks = run(fs, 1.25, 3.5, rules="excited")  # 2.25 s < 3 s
    ticks += run(fs, 3.75, 6, rules="relaxed")
    assert all(t.changed is None for t in ticks)
    assert fs.tick(6.25).current.emotion == "relaxed"


def test_change_after_persisting_3s_notifies_once():
    fs = FusionState(CFG)
    run(fs, 0, 1, rules="relaxed")
    ticks = run(fs, 1.25, 5, rules="excited")
    changed = [t for t in ticks if t.changed]
    assert len(changed) == 1 and changed[0].changed.emotion == "excited"
    assert changed[0].current.ts == pytest.approx(4.25)  # 1.25 + 3.0
    assert [t.notify.emotion for t in ticks if t.notify] == ["excited"]


def test_cooldown_suppresses_repeat_then_allows_after_60s():
    fs = FusionState(CFG)
    run(fs, 0, 0, rules="relaxed")
    a = run(fs, 0.25, 4, rules="excited")
    run(fs, 4.25, 8, rules="relaxed")
    c = run(fs, 8.25, 12, rules="excited")  # within 60 s of first excited notify
    assert sum(t.notify is not None and t.notify.emotion == "excited" for t in a + c) == 1
    run(fs, 12.25, 70, rules="relaxed")
    d = run(fs, 70.25, 74, rules="excited")
    assert any(t.notify and t.notify.emotion == "excited" for t in d)


def test_always_notify_repeats_while_persisting_after_cooldown():
    fs = FusionState(StateConfig(cooldown_s=10.0))
    run(fs, 0, 0, rules="relaxed")
    ticks = run(fs, 0.25, 30, rules="fearful")
    assert len([t for t in ticks if t.notify]) == 3  # at ~3.25, ~13.25, ~23.25


def test_quiet_unknown_never_notifies():
    fs = FusionState(CFG)
    run(fs, 0, 0, rules="relaxed")
    ticks = run(fs, 0.25, 6, seen=False)
    assert any(t.changed and t.changed.emotion == "unknown" for t in ticks)
    assert all(t.notify is None for t in ticks)


def test_stale_llm_result_ignored():
    fs = FusionState(CFG)
    fs.on_frame(dog(0)); fs.on_rules(R("relaxed", ts=0)); fs.on_llm(L("excited", 0.9, ts=0))
    assert fs.tick(1).live.emotion == "excited"
    fs.on_frame(dog(13)); fs.on_rules(R("relaxed", ts=13))
    assert fs.tick(13).live.emotion == "relaxed"


def test_timeline_spans_are_contiguous():
    fs = FusionState(CFG)
    run(fs, 0, 1, rules="relaxed")
    run(fs, 1.25, 6, rules="excited")
    spans = fs.timeline()
    assert [s.emotion for s in spans] == ["relaxed", "excited"]
    assert spans[0].end == spans[1].start == pytest.approx(4.25)
    assert spans[1].end == pytest.approx(6.0)
