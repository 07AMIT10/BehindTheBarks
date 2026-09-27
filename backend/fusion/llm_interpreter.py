"""Provider-agnostic LLM interpreter (Groq | OpenRouter | any OpenAI-compatible endpoint).

Provider/model/vision come only from LLMSettings (env). Returns None on timeout, error or bad output;
the caller then keeps the rules label. Every call is logged as one JSON line on logger "llm".
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from typing import Any

from backend.contracts import AudioEvent, Features, LLMResult, RulesLabel
from backend.fusion.llm_parse import parse_llm_reply
from backend.fusion.prompts import SYSTEM_PROMPT, build_user_content, encode_frame
from backend.web.settings import LLMSettings

log = logging.getLogger("llm")


class LLMInterpreter:
    def __init__(self, settings: LLMSettings, client: Any = None) -> None:
        self.s = settings
        self.json_mode = settings.json_mode
        self.last_call: dict | None = None
        self._client = client
        if self._client is None and settings.enabled:
            from openai import AsyncOpenAI  # imported lazily: tests and demo mode never need it

            self._client = AsyncOpenAI(base_url=settings.base_url, api_key=settings.api_key, max_retries=0,
                                       timeout=settings.timeout_s, default_headers=settings.extra_headers or None)

    @property
    def enabled(self) -> bool:
        return self.s.enabled and self._client is not None

    async def interpret(self, *, trigger: str, now: float, features: list[tuple[float, Features]],
                        audio: list[AudioEvent], rules: RulesLabel | None, jpeg: bytes | None) -> LLMResult | None:
        if not self.enabled:
            return None
        image = encode_frame(jpeg, self.s.image_max_side) if (self.s.vision and jpeg) else None
        kw: dict[str, Any] = {
            "model": self.s.model,
            "messages": [{"role": "system", "content": SYSTEM_PROMPT},
                         {"role": "user", "content": build_user_content(features, audio, rules, image, now)}],
            "temperature": self.s.temperature,
            "max_tokens": self.s.max_tokens,
        }
        if self.json_mode:
            kw["response_format"] = {"type": "json_object"}
        t0 = time.perf_counter()
        outcome, usage, result = "ok", None, None
        try:
            resp = await asyncio.wait_for(self._client.chat.completions.create(**kw), timeout=self.s.timeout_s)
            usage = getattr(resp, "usage", None)
            parsed = parse_llm_reply(resp.choices[0].message.content)
            if parsed is None:
                outcome = "bad_output"
            else:
                result = LLMResult(ts=now, emotion=parsed.emotion, confidence=parsed.confidence,
                                   reason=parsed.reason, provider=self.s.provider, model=self.s.model,
                                   latency_ms=round((time.perf_counter() - t0) * 1000, 1), trigger=trigger)
        except asyncio.TimeoutError:
            outcome = "timeout"
        except Exception as exc:  # noqa: BLE001 - any provider failure falls back to rules
            outcome = "error"
            if self.json_mode and type(exc).__name__ == "BadRequestError":
                self.json_mode = False
            log.warning("llm error: %s: %s", type(exc).__name__, exc)
        self.last_call = {
            "ts": now, "provider": self.s.provider, "model": self.s.model, "trigger": trigger,
            "latency_ms": round((time.perf_counter() - t0) * 1000, 1), "outcome": outcome,
            "vision": image is not None,
            "prompt_tokens": getattr(usage, "prompt_tokens", None),
            "completion_tokens": getattr(usage, "completion_tokens", None),
            "total_tokens": getattr(usage, "total_tokens", None),
        }
        log.info(json.dumps(self.last_call))
        return result
