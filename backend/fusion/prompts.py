"""Prompt text and request building for the LLM interpreter. Iterate on wording here only."""

from __future__ import annotations

import base64
import json

import cv2
import numpy as np

from backend.contracts import EMOTIONS, AudioEvent, Features, RulesLabel

SYSTEM_PROMPT = f"""You interpret a dog's emotional state for a pet-monitoring dashboard.
A static camera watches the dog's feeding area; a microphone hears it.
You receive: recent posture features from a vision model, recent dog sounds, the label from a
heuristic rules engine, and (sometimes) one camera frame.

Answer with ONLY a JSON object, no prose, no code fences:
{{"emotion": "<one of: {', '.join(EMOTIONS)}>", "confidence": <0.0-1.0>, "reason": "<one sentence>"}}

Rules:
- "emotion" must be exactly one word from the list above.
- "reason" cites observable cues only: posture, tail, ears, mouth, body, movement, sounds. Never guess
  about the owner, the dog's history or anything not visible or audible.
- If the dog is not clearly visible, answer "unknown" with low confidence.
- Features may be null (not measured); do not treat null as evidence.
- Be calibrated: use confidence >= 0.7 only when the cues clearly agree."""


def encode_frame(jpeg: bytes, max_side: int) -> str | None:
    img = cv2.imdecode(np.frombuffer(jpeg, np.uint8), cv2.IMREAD_COLOR) if jpeg else None
    if img is None:
        return None
    h, w = img.shape[:2]
    scale = max_side / max(h, w)
    if scale < 1.0:
        img = cv2.resize(img, (round(w * scale), round(h * scale)), interpolation=cv2.INTER_AREA)
    ok, buf = cv2.imencode(".jpg", img, [cv2.IMWRITE_JPEG_QUALITY, 80])
    return base64.b64encode(buf.tobytes()).decode("ascii") if ok else None


def build_user_content(features: list[tuple[float, Features]], audio: list[AudioEvent],
                       rules: RulesLabel | None, image_b64: str | None, now: float) -> str | list[dict]:
    payload = {
        "features": [
            {"t_minus_s": round(now - ts, 2), **{k: v for k, v in f.model_dump().items() if v is not None}}
            for ts, f in features
        ],
        "recent_sounds": [{"t_minus_s": round(now - a.ts, 2), "label": a.label, "score": round(a.score, 2)}
                          for a in audio],
        "rules_label": None if rules is None else {"emotion": rules.emotion, "confidence": round(rules.confidence, 2)},
    }
    text = "Observations (tail_height -1 tucked..1 high; others 0..1):\n" + json.dumps(payload)
    if image_b64 is None:
        return text
    return [{"type": "text", "text": text},
            {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{image_b64}"}}]
