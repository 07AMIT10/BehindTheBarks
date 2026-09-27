"""JSON contracts shared between Data (vision/audio/rules) and Web (fusion/dashboard).

Mirrors the "JSON contracts" section of CLAUDE.md exactly. Change a field only after agreeing it
with the consumer, and update CLAUDE.md in the same commit.

EmotionState and LLMResult (fusion -> dashboard) are owned by Web.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

Emotion = Literal[
    "happy",
    "excited",
    "relaxed",
    "anxious",
    "fearful",
    "aggressive",
    "disinterested",
    "unknown",
]
EMOTIONS: tuple[str, ...] = Emotion.__args__  # type: ignore[attr-defined]

EarPosition = Literal["up", "neutral", "back", "unknown"]
AudioLabel = Literal["bark", "yip", "growl", "whimper", "howl", "silence", "other"]


class _Contract(BaseModel):
    model_config = ConfigDict(extra="forbid")

    def to_json(self) -> str:
        """Serialise to a compact JSON string (nulls kept, so the shape is always complete)."""
        return self.model_dump_json()


class Features(_Contract):
    """Per-frame features. Any feature is None if its keypoints are missing or low confidence."""

    tail_height: float | None = Field(None, ge=-1.0, le=1.0)  # -1 tucked .. 1 high
    tail_wag_hz: float | None = Field(None, ge=0.0)
    ear_position: EarPosition | None = None
    mouth_open: float | None = Field(None, ge=0.0, le=1.0)
    body_lowering: float | None = Field(None, ge=0.0, le=1.0)
    motion_energy: float | None = Field(None, ge=0.0, le=1.0)
    in_feeding_zone: bool | None = None


class FrameEvent(_Contract):
    """Vision -> fusion, ~5-10 per second. bbox/keypoints are in full-frame pixel coords."""

    ts: float
    source: str  # "live" for the camera; other values allowed for replayed clips
    dog_detected: bool
    bbox: tuple[float, float, float, float] | None = None  # x1, y1, x2, y2
    bbox_conf: float | None = Field(None, ge=0.0, le=1.0)
    # name -> [x, y, conf]; a keypoint that is missing or below threshold is None, never guessed.
    body_keypoints: dict[str, tuple[float, float, float] | None] = Field(default_factory=dict)
    face_landmarks: list[tuple[float, float]] | None = None
    features: Features = Field(default_factory=Features)

    @field_validator("body_keypoints")
    @classmethod
    def _keypoint_conf_in_range(cls, v):
        for name, kp in v.items():
            if kp is not None and not 0.0 <= kp[2] <= 1.0:
                raise ValueError(f"keypoint {name!r} confidence {kp[2]} outside 0..1")
        return v


class AudioEvent(_Contract):
    """Audio -> fusion, one per YAMNet window (~0.96 s)."""

    ts: float
    label: AudioLabel
    score: float = Field(ge=0.0, le=1.0)


class RulesLabel(_Contract):
    """Rules engine output, delivered through Pipeline's on_rules_label callback."""

    ts: float
    emotion: Emotion
    confidence: float = Field(ge=0.0, le=1.0)
    scores: dict[Emotion, float]

    @field_validator("scores")
    @classmethod
    def _scores_in_range(cls, v):
        for emotion, score in v.items():
            if not 0.0 <= score <= 1.0:
                raise ValueError(f"score for {emotion!r} is {score}, outside 0..1")
        return v


class EmotionState(_Contract):
    """Fusion -> dashboard / notifier (owned by Web). `snapshot` is a base64 JPEG or URL, or None."""

    ts: float
    emotion: Emotion
    confidence: float = Field(ge=0.0, le=1.0)
    source: Literal["rules", "llm", "fused"]
    reason: str
    snapshot: str | None = None


class LLMResult(_Contract):
    """One parsed LLM interpretation (Web-internal, broadcast to the dashboard as type "llm")."""

    ts: float
    emotion: Emotion
    confidence: float = Field(ge=0.0, le=1.0)
    reason: str
    provider: str
    model: str
    latency_ms: float
    trigger: str
