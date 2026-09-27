import asyncio
import threading

from backend.contracts import LLMResult
from backend.demo.mock_pipeline import MockPipeline
from backend.notify.base import NotifyResult
from backend.web.hub import Hub
from backend.web.runtime import Runtime
from backend.web.settings import load_config

T0 = 1_000_000.0


class FakeLLM:
    enabled = True

    def __init__(self, emotion="excited", conf=0.9, fail=False):
        self.calls, self.emotion, self.conf, self.fail = [], emotion, conf, fail
        self.last_call = None

    async def interpret(self, *, trigger, now, features, audio, rules, jpeg):
        self.calls.append(dict(trigger=trigger, n_features=len(features), n_audio=len(audio), jpeg=jpeg is not None))
        self.last_call = {"outcome": "error" if self.fail else "ok"}
        if self.fail:
            return None
        return LLMResult(ts=now, emotion=self.emotion, confidence=self.conf, reason="LLM.", provider="p",
                         model="m", latency_ms=1.0, trigger=trigger)


class FakeNotifier:
    def __init__(self):
        self.sent = []

    async def send(self, state, jpeg):
        self.sent.append(state)
        return NotifyResult("dashboard_only", "dashboard", "would send to owner")


def make(tmp_path, llm=None):
    cfg = load_config(tmp_path / "none.yaml", env={})
    clock = {"t": T0}
    mp = MockPipeline(cfg, clock=lambda: clock["t"])
    hub = Hub(clock=lambda: clock["t"], frame_fps=1000)
    q = hub.subscribe()
    rt = Runtime(cfg, mp, llm or FakeLLM(), FakeNotifier(), hub, clock=lambda: clock["t"])
    return rt, mp, hub, q, clock


def feed(rt, mp, t):
    f, a, r = mp.step(t)
    rt.on_frame(f)
    for x in a:
        rt.on_audio(x)
    rt.on_rules(r)


def drain(q):
    out = []
    while not q.empty():
        out.append(q.get_nowait())
    return out


async def settle(rt):
    while rt.pending:
        await asyncio.gather(*list(rt.pending), return_exceptions=True)


async def test_callbacks_publish_frame_audio_rules(tmp_path):
    rt, mp, hub, q, _ = make(tmp_path)
    feed(rt, mp, T0 + 12.0)  # excited: yip on first step
    assert {m["type"] for m in drain(q)} >= {"frame", "audio", "rules"}


async def test_step_emits_emotion_and_heartbeat_llm(tmp_path):
    llm = FakeLLM(emotion="relaxed")
    rt, mp, hub, q, clock = make(tmp_path, llm)
    for i in range(8):
        clock["t"] = T0 + i * 0.25
        feed(rt, mp, clock["t"])
    rt.step(clock["t"])
    await settle(rt)
    msgs = drain(q)
    assert any(m["type"] == "emotion" for m in msgs)
    assert llm.calls[0]["trigger"] == "heartbeat" and llm.calls[0]["n_features"] > 0 and llm.calls[0]["jpeg"]
    assert any(m["type"] == "llm" and m["data"]["emotion"] == "relaxed" for m in msgs)


async def test_treat_jumps_mock_publishes_and_triggers_llm(tmp_path):
    llm = FakeLLM()
    rt, mp, hub, q, clock = make(tmp_path, llm)
    rt.step(T0)
    await settle(rt)  # heartbeat at T0
    clock["t"] = T0 + 50
    assert mp.phase_at(clock["t"]) == "disinterested"  # 42-56 s
    rt.treat()
    assert mp.phase_at(clock["t"] + 0.1) == "excited"
    rt.step(clock["t"])
    await settle(rt)
    assert [c["trigger"] for c in llm.calls] == ["heartbeat", "treat"]
    assert any(m["type"] == "treat" for m in drain(q))


async def test_llm_failure_is_published_not_raised(tmp_path):
    rt, mp, hub, q, clock = make(tmp_path, FakeLLM(fail=True))
    feed(rt, mp, T0)
    rt.step(T0)
    await settle(rt)
    llm_msgs = [m for m in drain(q) if m["type"] == "llm"]
    assert llm_msgs and llm_msgs[0]["meta"] == {"ok": False}


async def test_state_change_notifies_and_publishes(tmp_path):
    # LLM disagrees at low confidence, so the rules drive the change (Decision 4: first state never notifies)
    rt, mp, hub, q, clock = make(tmp_path, FakeLLM(emotion="relaxed", conf=0.5))
    for i in range(4):  # 1 s of relaxed -> first state
        clock["t"] = T0 + i * 0.25
        feed(rt, mp, clock["t"]); rt.step(clock["t"]); await settle(rt)
    for i in range(20):  # 5 s of excited -> change persists 3 s -> notify
        clock["t"] = T0 + 12.0 + i * 0.25
        feed(rt, mp, clock["t"]); rt.step(clock["t"]); await settle(rt)
    notes = [m for m in drain(q) if m["type"] == "notification"]
    assert notes and notes[0]["data"]["state"]["emotion"] == "excited"
    assert notes[0]["data"]["status"] == "dashboard_only"
    assert notes[0]["data"]["state"]["snapshot"].startswith("data:image/jpeg;base64,")
    assert rt.notifier.sent[0].emotion == "excited"


async def test_callbacks_from_other_thread_are_marshalled(tmp_path):
    rt, mp, hub, q, clock = make(tmp_path)
    rt.bind_loop(asyncio.get_running_loop())
    f, _, _ = mp.step(T0)
    th = threading.Thread(target=rt.on_frame, args=(f,))
    th.start(); th.join()
    assert q.empty()  # not handled on the foreign thread
    await asyncio.sleep(0)
    assert [m["type"] for m in drain(q)] == ["frame"]


async def test_start_stop_with_real_loop(tmp_path):
    rt, mp, hub, q, clock = make(tmp_path)
    mp._clock = __import__("time").time
    rt.clock = __import__("time").time
    await rt.start()
    await asyncio.sleep(0.3)
    await rt.stop()
    await rt.stop()
    assert mp.status()["state"] == "stopped"
    st = rt.status()
    assert set(st) == {"pipeline", "pipeline_status", "demo_mode", "llm", "notify_mode", "clients", "fps", "profile"}
    assert st["profile"]["dog_name"] == "Bruno" and st["llm"]["online"] is True
