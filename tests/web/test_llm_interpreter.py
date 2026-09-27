import asyncio
from types import SimpleNamespace

from backend.fusion.llm_interpreter import LLMInterpreter
from backend.web.settings import LLMSettings

S = LLMSettings(provider="groq", base_url="u", api_key="k", model="m", vision=False, timeout_s=0.2)


class FakeClient:
    def __init__(self, reply=None, delay=0.0, exc=None):
        self.reply, self.delay, self.exc, self.calls = reply, delay, exc, []
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self._create))

    async def _create(self, **kw):
        self.calls.append(kw)
        await asyncio.sleep(self.delay)
        if self.exc:
            raise self.exc
        msg = SimpleNamespace(content=self.reply)
        usage = SimpleNamespace(prompt_tokens=10, completion_tokens=5, total_tokens=15)
        return SimpleNamespace(choices=[SimpleNamespace(message=msg)], usage=usage)


async def _go(client, settings=S, jpeg=None):
    it = LLMInterpreter(settings, client=client)
    return it, await it.interpret(trigger="treat", now=100.0, features=[], audio=[], rules=None, jpeg=jpeg)


async def test_good_reply_becomes_llm_result():
    it, r = await _go(FakeClient('{"emotion":"happy","confidence":0.9,"reason":"Loose mouth."}'))
    assert (r.emotion, r.confidence, r.provider, r.model, r.trigger) == ("happy", 0.9, "groq", "m", "treat")
    assert it.last_call["outcome"] == "ok" and it.last_call["total_tokens"] == 15


async def test_request_shape_json_mode_and_no_model_hardcoding():
    c = FakeClient('{"emotion":"happy","confidence":0.9,"reason":"x"}')
    await _go(c)
    kw = c.calls[0]
    assert kw["model"] == "m" and kw["response_format"] == {"type": "json_object"}
    assert kw["messages"][0]["role"] == "system" and isinstance(kw["messages"][1]["content"], str)


async def test_timeout_returns_none_and_logs():
    it, r = await _go(FakeClient("{}", delay=1.0))
    assert r is None and it.last_call["outcome"] == "timeout"


async def test_bad_output_returns_none():
    it, r = await _go(FakeClient("I think the dog is sad"))
    assert r is None and it.last_call["outcome"] == "bad_output"


async def test_error_returns_none_and_json_mode_disabled_on_bad_request():
    class BadRequestError(Exception):
        pass
    it, r = await _go(FakeClient(exc=BadRequestError("response_format not supported")))
    assert r is None and it.last_call["outcome"] == "error"
    assert it.json_mode is False  # next call retries without JSON mode


async def test_disabled_without_settings():
    it = LLMInterpreter(LLMSettings(), client=FakeClient("{}"))
    assert not it.enabled
    assert await it.interpret(trigger="x", now=0, features=[], audio=[], rules=None, jpeg=None) is None
