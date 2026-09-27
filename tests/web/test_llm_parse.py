import pytest

from backend.fusion.llm_parse import parse_llm_reply

GOOD = '{"emotion": "excited", "confidence": 0.8, "reason": "Fast tail wag."}'


@pytest.mark.parametrize("text", [
    GOOD,
    f"```json\n{GOOD}\n```",
    f"```\n{GOOD}\n```",
    f"Sure! Here you go:\n{GOOD}\nHope that helps.",
    GOOD.replace("excited", " Excited "),
])
def test_accepts_messy_but_valid(text):
    r = parse_llm_reply(text)
    assert r and (r.emotion, r.confidence, r.reason) == ("excited", 0.8, "Fast tail wag.")


@pytest.mark.parametrize("text", [
    None, "", "no json here", "{not json}", '{"emotion": "sad", "confidence": 0.9, "reason": "x"}',
    '{"emotion": "happy", "confidence": "high", "reason": "x"}', '{"emotion": "happy"}',
    '["happy", 0.9]', '{"emotion": "happy", "confidence": NaN, "reason": "x"}',
])
def test_rejects_invalid(text):
    assert parse_llm_reply(text) is None


@pytest.mark.parametrize("raw,expected", [(1.7, 1.0), (-0.2, 0.0), (85, 0.85), (100, 1.0), ("0.4", 0.4)])
def test_confidence_clamped_or_percent(raw, expected):
    r = parse_llm_reply(f'{{"emotion": "happy", "confidence": {raw!r}, "reason": "x"}}'.replace("'", '"'))
    assert r.confidence == pytest.approx(expected)


def test_reason_single_line_trimmed_and_defaulted():
    r = parse_llm_reply('{"emotion": "happy", "confidence": 0.5, "reason": "line one\\nline two"}')
    assert r.reason == "line one"
    assert parse_llm_reply('{"emotion": "happy", "confidence": 0.5}').reason == "No reason given."
    long = parse_llm_reply('{"emotion":"happy","confidence":0.5,"reason":"' + "a" * 500 + '"}')
    assert len(long.reason) == 240


def test_picks_first_object_when_two():
    r = parse_llm_reply('{"emotion":"happy","confidence":0.5,"reason":"a"} {"emotion":"sad"}')
    assert r.emotion == "happy"
