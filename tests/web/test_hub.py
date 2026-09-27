import json

import pytest

from backend.contracts import AudioEvent
from backend.web.event_log import EventLog
from backend.web.hub import Hub


class Clock:
    def __init__(self, t=100.0):
        self.t = t

    def __call__(self):
        return self.t


def test_envelope_and_model_serialisation():
    hub = Hub(clock=Clock())
    env = hub.publish("audio", AudioEvent(ts=1.0, label="bark", score=0.9))
    assert env["type"] == "audio" and env["seq"] == 1 and env["data"]["label"] == "bark" and "meta" not in env
    assert hub.publish("treat", {"ts": 2.0}, meta={"by": "button"})["meta"] == {"by": "button"}


def test_unknown_type_rejected():
    with pytest.raises(ValueError):
        Hub().publish("nope", {})


def test_slow_client_drops_oldest_never_blocks():
    hub = Hub(client_queue=3, clock=Clock())
    q = hub.subscribe()
    for i in range(5):
        hub.publish("treat", {"i": i})
    got = [q.get_nowait()["data"]["i"] for _ in range(q.qsize())]
    assert got == [2, 3, 4]


def test_history_excludes_frames_and_filters_since():
    c = Clock()
    hub = Hub(clock=c)
    hub.publish("treat", {"i": 1})
    hub.publish("frame", {"x": 1})
    hub.publish("treat", {"i": 2})
    assert [m["data"]["i"] for m in hub.history()] == [1, 2]
    assert [m["data"]["i"] for m in hub.history(since=1)] == [2]


def test_frame_throttle():
    c = Clock()
    hub = Hub(frame_fps=4, clock=c)
    q = hub.subscribe()
    assert hub.publish("frame", {}) is not None
    c.t += 0.1
    assert hub.publish("frame", {}) is None  # < 0.25 s
    c.t += 0.2
    assert hub.publish("frame", {}) is not None
    assert q.qsize() == 2


def test_unsubscribe_and_count():
    hub = Hub()
    q = hub.subscribe()
    assert hub.client_count == 1
    hub.unsubscribe(q)
    hub.unsubscribe(q)  # idempotent
    assert hub.client_count == 0


def test_event_log_writes_jsonl(tmp_path):
    log = EventLog(tmp_path / "out", clock=lambda: 0.0)
    hub = Hub(log=log, clock=Clock())
    hub.publish("treat", {"i": 1})
    hub.publish("frame", {"x": 1})
    log.close()
    lines = [json.loads(l) for l in log.path.read_text().splitlines()]
    assert [m["type"] for m in lines] == ["treat", "frame"]
    assert log.path.name.startswith("session-") and log.path.suffix == ".jsonl"
