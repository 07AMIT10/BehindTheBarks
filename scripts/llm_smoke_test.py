"""One real call to the configured provider. Usage (reads .env):
    .venv/bin/python scripts/llm_smoke_test.py [path/to/frame.jpg]
Run once with Groq and once with OpenRouter env settings; prints the parsed result and latency."""

import asyncio
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dotenv import load_dotenv  # noqa: E402

from backend.contracts import EMOTIONS, AudioEvent, Features, RulesLabel
from backend.fusion.llm_interpreter import LLMInterpreter
from backend.web.settings import LLMSettings, load_config


async def main() -> None:
    load_dotenv()
    s = LLMSettings.from_config(load_config())
    if not s.enabled:
        sys.exit("LLM not configured: set LLM_BASE_URL, LLM_API_KEY, LLM_MODEL in .env (and DEMO_MODE=0)")
    jpeg = open(sys.argv[1], "rb").read() if len(sys.argv) > 1 else None
    now = time.time()
    feats = [(now - 1, Features(tail_height=0.6, tail_wag_hz=3.8, motion_energy=0.8, ear_position="up"))]
    rules = RulesLabel(ts=now, emotion="excited", confidence=0.6, scores={e: 0.1 for e in EMOTIONS})
    it = LLMInterpreter(s)
    r = await it.interpret(trigger="smoke", now=now, features=feats,
                           audio=[AudioEvent(ts=now - 0.5, label="yip", score=0.8)], rules=rules, jpeg=jpeg)
    print(json.dumps(it.last_call, indent=2))
    print("RESULT:", r.to_json() if r else None)


if __name__ == "__main__":
    asyncio.run(main())
