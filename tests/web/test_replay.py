from backend.contracts import LLMResult
from backend.demo.mock_pipeline import MockPipeline
from backend.demo.replay import replay_clip


class FakeLLM:
    enabled = True

    def __init__(self):
        self.calls = []
        self.last_call = None

    async def interpret(self, *, trigger, now, features, audio, rules, jpeg):
        self.calls.append(trigger)
        self.last_call = {"outcome": "ok"}
        return LLMResult(ts=now, emotion="excited", confidence=0.9, reason="Fast wag.", provider="p",
                         model="m", latency_ms=1.0, trigger=trigger)


async def test_replay_produces_states_notifications_treats():
    mp = MockPipeline({"data": {"fps": 8}}, clock=lambda: 0.0)
    evs = []
    for i in range(160):
        t = round(i / 8, 3)
        f, a, r = mp.step(t)
        evs.append({"t": t, "type": "frame", "data": f.model_dump(mode="json")})
        evs += [{"t": t, "type": "audio", "data": x.model_dump(mode="json")} for x in a]
        evs.append({"t": t, "type": "rules", "data": r.model_dump(mode="json")})
    evs.append({"t": 12.0, "type": "treat", "data": {"ts": 12.0}})
    tl = await replay_clip(evs, lambda t: b"\xff\xd8fake", FakeLLM())
    assert tl["duration_s"] == 19.875
    emotions = [s["emotion"] for s in tl["states"]]
    assert emotions[0] == "relaxed" and "excited" in emotions
    assert tl["treats"] == [12.0]
    assert tl["llm"] and all(x["trigger"] for x in tl["llm"])
    assert isinstance(tl["notifications"], list)
    live_ts = [s["t"] for s in tl["live"]]
    assert live_ts == sorted(live_ts) and live_ts[-1] <= 19.875
