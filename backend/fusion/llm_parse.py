"""Defensive parsing of the LLM reply. Never raises; returns None on anything unusable."""

from __future__ import annotations

import json
import math
import re
from typing import NamedTuple

from backend.contracts import EMOTIONS

_FENCE = re.compile(r"```(?:json)?", re.IGNORECASE)
MAX_REASON = 240


class ParsedReply(NamedTuple):
    emotion: str
    confidence: float
    reason: str


def _first_object(s: str):
    dec = json.JSONDecoder()
    for i, ch in enumerate(s):
        if ch == "{":
            try:
                obj, _ = dec.raw_decode(s[i:])
                return obj
            except json.JSONDecodeError:
                continue
    return None


def parse_llm_reply(text: str | None) -> ParsedReply | None:
    if not text:
        return None
    obj = _first_object(_FENCE.sub("", text))
    if not isinstance(obj, dict):
        return None
    emotion = str(obj.get("emotion", "")).strip().lower()
    if emotion not in EMOTIONS:
        return None
    try:
        conf = float(obj.get("confidence"))
    except (TypeError, ValueError):
        return None
    if math.isnan(conf) or math.isinf(conf):
        return None
    if 1.0 < conf <= 100.0 and float(conf).is_integer():
        conf /= 100.0  # "85" meaning 85 %
    conf = min(1.0, max(0.0, conf))
    reason = str(obj.get("reason") or "").strip()
    reason = (reason.splitlines()[0].strip() if reason else "") or "No reason given."
    return ParsedReply(emotion, conf, reason[:MAX_REASON])
