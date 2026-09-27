import inspect

import pytest
from pydantic import ValidationError

from backend.contracts import EmotionState, LLMResult
from backend.pipeline import PIPELINE_METHODS, Pipeline

EXAMPLE = {"ts": 1727340001.0, "emotion": "excited", "confidence": 0.78, "source": "fused",
           "reason": "High, fast tail wag and open mouth as the treat drops; two short yips.",
           "snapshot": "base64 or URL"}


def test_emotion_state_matches_claude_md_example():
    s = EmotionState.model_validate(EXAMPLE)
    assert EmotionState.model_validate_json(s.to_json()) == s


@pytest.mark.parametrize("bad", [{"emotion": "sad"}, {"confidence": 1.2}, {"source": "gpt"}, {"extra": 1}])
def test_emotion_state_rejects_bad(bad):
    with pytest.raises(ValidationError):
        EmotionState.model_validate({**EXAMPLE, **bad})


def test_llm_result_roundtrip():
    r = LLMResult(ts=1.0, emotion="happy", confidence=0.9, reason="Loose mouth.", provider="groq",
                  model="m", latency_ms=812.5, trigger="treat")
    assert LLMResult.model_validate_json(r.to_json()) == r


def test_pipeline_interface_matches_claude_md():
    assert PIPELINE_METHODS == ("run", "latest_frame_jpeg", "mark_treat", "ingest_frame",
                                "ingest_audio", "status", "stop")
    for name in PIPELINE_METHODS:
        assert callable(getattr(Pipeline, name))
    assert list(inspect.signature(Pipeline.ingest_audio).parameters) == ["self", "pcm16", "sample_rate", "ts"]
