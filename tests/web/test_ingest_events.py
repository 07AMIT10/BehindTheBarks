import asyncio
import json
import logging
import time
import typing

import cv2
import numpy as np
import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from backend.contracts import EMOTIONS
from backend.demo.mock_pipeline import MockPipeline
from backend.main import build_pipeline, create_app
from backend.notify.dashboard import DashboardOnlyNotifier
from backend.pipeline import PIPELINE_METHODS
from backend.web.ingest_events import (KIND_PREVIEW, PROTO, ClockOffset, RemoteHello, RemoteSession,
                                       bind_pipeline, parse_remote_hello)
from backend.web.remote_pipeline import RemotePipeline
from backend.web.runtime import Runtime
from backend.web.settings import load_config

PHONE_T = 1_700_000_000.0  # what the phone's clock says; the server's clock is far ahead of it


class NoLLM:
    enabled = False
    last_call = None


class Clock:
    def __init__(self, t=PHONE_T + 4000.0):
        self.t = t

    def __call__(self):
        return self.t


class SpyPipeline:
    def __init__(self, fail=False):
        self.frames, self.fail = [], fail

    def ingest_frame(self, jpeg, ts):
        if self.fail:
            raise RuntimeError("boom")
        self.frames.append((jpeg, ts))

    def ingest_audio(self, pcm16, sample_rate, ts):
        pass


class SpyRuntime:
    def __init__(self):
        self.frames, self.audio, self.rules, self.treats = [], [], [], []

    def on_frame(self, ev):
        self.frames.append(ev)

    def on_audio(self, ev):
        self.audio.append(ev)

    def on_rules(self, ev):
        self.rules.append(ev)

    def treat(self, now=None):
        self.treats.append(now)
        return now


class BoomRuntime(SpyRuntime):
    """A Runtime whose every callback raises, the way EventLog.write raises on a full disk."""

    def __init__(self, exc: Exception | None = None) -> None:
        super().__init__()
        self.exc = exc if exc is not None else OSError("event log write failed")

    def on_frame(self, ev):
        raise self.exc

    def on_audio(self, ev):
        raise self.exc

    def on_rules(self, ev):
        raise self.exc

    def treat(self, now=None):
        raise self.exc


# -- payloads ---------------------------------------------------------------------------------
def hello_msg(**over):
    msg = {"type": "hello", "proto": PROTO, "device": "Pixel 7a", "phone_time": PHONE_T,
           "models": {"detect": "yolo26n-320", "pose": "rtmpose-s"}, "profile": "mobile"}
    msg.update(over)
    return json.dumps(msg)


def frame_data(ts, **over):
    data = {"ts": ts, "source": "live", "dog_detected": True, "bbox": [1.0, 2.0, 30.0, 40.0],
            "bbox_conf": 0.9, "body_keypoints": {"tail_base": [10.0, 20.0, 0.9]}, "face_landmarks": None,
            "features": {"tail_height": 0.4, "tail_wag_hz": 2.1, "ear_position": "up", "mouth_open": 0.6,
                         "body_lowering": 0.1, "motion_energy": 0.7, "in_feeding_zone": True}}
    return {**data, **over}


def rules_data(ts, emotion="happy"):
    return {"ts": ts, "emotion": emotion, "confidence": 0.8,
            "scores": {e: (0.8 if e == emotion else 0.1) for e in EMOTIONS}}


def audio_data(ts, label="bark"):
    return {"ts": ts, "label": label, "score": 0.8}


def envelope(kind, data):
    return json.dumps({"type": kind, "data": data})


def phone_jpeg() -> bytes:
    img = np.zeros((480, 640, 3), np.uint8)
    img[:, :, 2] = 200
    ok, buf = cv2.imencode(".jpg", img, [cv2.IMWRITE_JPEG_QUALITY, 70])
    return buf.tobytes()


def wait_for(pred, timeout=3.0):
    end = time.time() + timeout
    while time.time() < end:
        if pred():
            return True
        time.sleep(0.02)
    return False


def stub_cfg(remote: bool = True) -> dict:
    cfg = load_config("none.yaml", env={})
    cfg["web"]["pipeline"] = "remote" if remote else "mock"
    return cfg


def cfg_for(tmp_path, **web):
    cfg = stub_cfg()
    cfg["web"]["log_dir"] = str(tmp_path / "out")
    for k, v in web.items():
        cfg["web"][k] = {**cfg["web"].get(k, {}), **v} if isinstance(v, dict) else v
    return cfg


def remote_client(tmp_path, pipeline=None, **web):
    cfg = cfg_for(tmp_path, **web)
    p = pipeline or RemotePipeline(cfg)
    app = create_app(cfg, pipeline=p, interpreter=NoLLM(), notifier=DashboardOnlyNotifier())
    return TestClient(app), p


def session(clock=None, runtime=None, **over):
    clock = clock or Clock()
    return RemoteSession(parse_remote_hello(hello_msg()), over.pop("pipeline", None) or SpyPipeline(),
                         runtime or SpyRuntime(), clock=clock, **over), clock


def spy_runtime(rt):
    """Call-through recorders, so the Runtime's own state and hub fan-out still happen."""
    seen: dict[str, list] = {"frame": [], "audio": [], "rules": [], "treat": []}
    for name in seen:
        if name == "treat":
            continue
        orig = getattr(rt, f"on_{name}")

        def wrap(ev, _orig=orig, _name=name):
            seen[_name].append(ev)
            _orig(ev)

        setattr(rt, f"on_{name}", wrap)
    orig_treat = rt.treat

    def treat(now=None):
        seen["treat"].append(now)
        return orig_treat(now)

    rt.treat = treat
    return seen


def emotion_events(c):
    return [m for m in c.get("/events").json()["events"] if m["type"] == "emotion"]


def next_phone(dash, pred, tries=400):
    for _ in range(tries):
        m = dash.receive_json()
        if m["type"] == "status" and (m["data"].get("phone") or {}) and pred(m["data"]["phone"]):
            return m["data"]["phone"]
    raise AssertionError("no matching phone status arrived")


# -- Step 1.1: hello --------------------------------------------------------------------------
def test_parse_remote_hello_minimal_and_full():
    h = parse_remote_hello(json.dumps({"type": "hello", "device": "Pixel 7a", "phone_time": PHONE_T}))
    assert h == RemoteHello(device="Pixel 7a", phone_time=PHONE_T, models={}, profile="mobile", proto=PROTO)
    h = parse_remote_hello(hello_msg(device="  " + "x" * 100 + " ", profile="mobile-v2"))
    assert h.device == "x" * 64 and h.profile == "mobile-v2" and h.models["pose"] == "rtmpose-s"


@pytest.mark.parametrize("text", [
    None, "", "not json", "[1, 2]", json.dumps({"type": "frame"}),
    hello_msg(device=""), hello_msg(device=5), hello_msg(proto=99),
    hello_msg(phone_time="now"), hello_msg(phone_time=True), hello_msg(phone_time=1.7e12),  # ms, not s
    hello_msg(models="yolo"), hello_msg(models={"a": {"deep": 1}}), hello_msg(profile=3),
])
def test_bad_remote_hellos_raise(text):
    with pytest.raises(ValueError) as exc:
        parse_remote_hello(text)
    assert "hello" in str(exc.value)


def test_hello_over_the_wire_is_acknowledged_with_the_server_clock(tmp_path):
    c, p = remote_client(tmp_path)
    with c, c.websocket_connect("/ingest-events") as ws:
        ws.send_text(hello_msg(phone_time=time.time() - 3600.0))
        ack = ws.receive_json()
        assert ack["type"] == "hello_ack" and abs(ack["server_time"] - time.time()) < 5.0
        # a frame stamped an hour ago on the phone's clock lands "now" on the server's
        ws.send_text(envelope("frame", frame_data(time.time() - 3600.0 + 1.0)))
        assert wait_for(lambda: p.status()["events"] == 1)
        assert p.status()["last_frame_age_s"] < 5.0 and p.status()["clock_offset_s"] == pytest.approx(3600.0, abs=5)


# -- Step 1.3: clock offset -------------------------------------------------------------------
def test_clock_offset_seeded_and_median_of_the_last_five():
    o = ClockOffset(samples=5)
    o.seed(1000.0, 400.0)  # the phone's clock is 600 s behind
    assert o.offset == 600.0 and o.count == 1 and o.rebase(500.0) == 1100.0
    for server, phone in ((2000.0, 1400.0), (3000.0, 2400.0), (4000.0, 2800.0), (5000.0, 4400.0),
                          (6000.0, 5400.0)):  # the third ping is a 1200 s outlier
        o.add(server, phone)
    assert o.count == 5 and o.offset == 600.0  # median of [600, 600, 1200, 600, 600]
    o.add(7000.0, 6400.0)
    assert o.count == 5 and o.offset == 600.0  # the window slid, so the outlier is gone


def test_clock_offset_even_window_averages_the_middle_two():
    o = ClockOffset(samples=4)
    for server, phone in ((10.0, 5.0), (20.0, 15.0), (30.0, 23.0), (40.0, 34.0)):  # 5, 5, 7, 6
        o.add(server, phone)
    assert o.offset == pytest.approx(5.5)


def test_session_seeds_the_offset_from_the_hello():
    s, clock = session()
    assert s.server_time_at_hello == clock.t and s.offset.offset == clock.t - PHONE_T
    assert s.handle_text(envelope("frame", frame_data(PHONE_T + 10.0))) == "frame"
    assert s.runtime.frames[0].ts == pytest.approx(clock.t + 10.0)


def test_ping_refreshes_the_offset():
    s, clock = session(ping_every_s=30.0)
    assert s.metrics(clock.t)["ping_due_s"] == 30.0
    for _ in range(2):  # the hello's own sample leaves the window as the pings come in
        clock.t += 5.0
        assert s.handle_text(json.dumps({"type": "ping", "phone_time": clock.t - 700.0})) == "ping"
    assert s.offset.offset == 700.0 and s.pings == 2 and s.offset.count == 3
    assert s.metrics(clock.t)["ping_due_s"] == 30.0
    clock.t += 31.0
    assert s.metrics(clock.t)["ping_due_s"] == pytest.approx(-1.0)  # overdue
    assert s.handle_text(json.dumps({"type": "ping"})) is None  # no phone_time -> dropped
    assert s.dropped == 1


def test_a_ping_is_answered_with_a_pong(tmp_path):
    c, _ = remote_client(tmp_path)
    now = time.time()
    with c, c.websocket_connect("/ingest-events") as ws:
        ws.send_text(hello_msg(phone_time=now))
        assert ws.receive_json()["type"] == "hello_ack"
        ws.send_text(json.dumps({"type": "ping", "phone_time": now}))
        pong = ws.receive_json()
    assert pong["type"] == "pong" and abs(pong["server_time"] - time.time()) < 5.0


# -- Step 1.2: envelopes reach the runtime ----------------------------------------------------
def test_events_reach_the_runtime_with_rebased_timestamps():
    s, clock = session()
    assert s.handle_text(envelope("frame", frame_data(PHONE_T + 1.0))) == "frame"
    clock.t += 1.0
    assert s.handle_text(envelope("audio", audio_data(PHONE_T + 2.0))) == "audio"
    clock.t += 1.0
    assert s.handle_text(envelope("rules", rules_data(PHONE_T + 3.0))) == "rules"
    clock.t += 1.0
    assert s.handle_text(envelope("treat", {"ts": PHONE_T + 4.0})) == "treat"
    clock.t += 1.0
    assert s.handle_text(envelope("treat", {})) == "treat"  # no ts: stamped at receipt
    assert s.runtime.frames[0].ts == pytest.approx(clock.t - 4.0)
    assert s.runtime.audio[0].ts == pytest.approx(clock.t - 3.0)
    assert s.runtime.rules[0].ts == pytest.approx(clock.t - 2.0)
    assert s.runtime.rules[0].emotion == "happy" and s.runtime.audio[0].label == "bark"
    assert s.runtime.treats == [pytest.approx(clock.t - 1.0), clock.t]
    assert (s.frames, s.audio_events, s.rules_labels, s.treats, s.dropped) == (1, 1, 1, 2, 0)
    assert s.events == 3


@pytest.mark.parametrize("text", [
    "", "not json", "[1, 2]", json.dumps({"type": "frame"}), json.dumps({"type": "frame", "data": []}),
    json.dumps({"type": "nope", "data": {}}), json.dumps({"type": 7, "data": {}}),
    envelope("frame", frame_data(PHONE_T, extra=1)),                                # extra="forbid"
    envelope("frame", {k: v for k, v in frame_data(PHONE_T).items() if k != "ts"}),  # no ts
    envelope("frame", frame_data("now")),                                            # ts not a number
    envelope("rules", rules_data(PHONE_T, emotion="sad")),                           # outside the vocabulary
    envelope("audio", audio_data(PHONE_T, label="moo")),
    envelope("treat", {"ts": "soon"}),
])
def test_invalid_envelopes_are_dropped_not_raised(text):
    s, _ = session()
    assert s.handle_text(text) is None
    assert s.dropped == 1 and s.events == 0
    assert s.runtime.frames == [] and s.runtime.treats == []


def test_disconnected_session_ignores_late_data_and_reports_its_metrics():
    s, clock = session()
    jpeg = phone_jpeg()
    for i in range(9):  # 8 events/s and 8 previews/s for 1 s
        clock.t += 0.125
        s.handle_text(envelope("frame", frame_data(PHONE_T + i)))
        s.handle(bytes([KIND_PREVIEW]) + jpeg)
    clock.t += 0.5
    snap = s.snapshot(clock.t)
    assert snap["fps"] == 8.0 and snap["preview_fps"] == 8.0 and snap["previews"] == 9
    assert snap["device"] == "Pixel 7a" and snap["transport"] == "ingest-events"
    assert snap["facing"] is None and snap["camera"] is True
    assert snap["profile"] == "mobile" and snap["proto"] == PROTO and snap["models"]["detect"] == "yolo26n-320"
    assert snap["clock_offset_s"] == 4000.0 and snap["clock_samples"] == 1  # from the hello
    assert snap["last_frame_age_s"] == 0.5 and snap["connected"] is True
    m = s.metrics(clock.t)
    assert m["events_s"] == 8.0 and m["audio_ok"] is False and m["last_preview_age_s"] == 0.5
    s.connected = False
    assert s.snapshot(clock.t)["fps"] == 0.0
    assert s.handle_text(envelope("frame", frame_data(PHONE_T))) is None
    assert s.handle(bytes([KIND_PREVIEW]) + jpeg) is None
    assert s.idle_s(clock.t) == pytest.approx(clock.t - s.last_msg_ts)


def test_envelopes_reach_the_runtime_and_the_hub(tmp_path):
    c, _ = remote_client(tmp_path)
    with c:
        seen = spy_runtime(c.app.state.rt)
        now = time.time()
        with c.websocket_connect("/ingest-events") as ws:
            ws.send_text(hello_msg(phone_time=now))
            assert ws.receive_json()["type"] == "hello_ack"
            for kind, data in (("frame", frame_data(now)), ("audio", audio_data(now)),
                               ("rules", rules_data(now)), ("treat", {"ts": now})):
                ws.send_text(envelope(kind, data))
            assert wait_for(lambda: len(seen["treat"]) == 1)
            assert len(seen["frame"]) == 1 and len(seen["audio"]) == 1 and len(seen["rules"]) == 1
            assert seen["rules"][0].emotion == "happy"
        assert wait_for(lambda: c.get("/status").json()["phone"] is None)  # its own Stop button
        history = c.get("/events").json()["events"]
    # audio, rules and treat are in the hub history by design; frame envelopes are live-only (Hub)
    assert {"audio", "rules", "treat"}.issubset({m["type"] for m in history})
    assert any(m["type"] == "rules" and m["data"]["emotion"] == "happy" for m in history)


# -- Step 1.4: preview JPEGs ------------------------------------------------------------------
def test_binary_preview_becomes_the_latest_frame():
    s, clock = session()
    jpeg = phone_jpeg()
    assert s.handle(bytes([KIND_PREVIEW]) + jpeg) == "preview"
    assert s.pipeline.frames == [(jpeg, clock.t)]
    assert s.previews == 1 and s.last_preview_ts == clock.t


@pytest.mark.parametrize("data", [b"", b"\x01", b"\x02abc", b"\x09abc"])
def test_binary_drops_everything_but_the_preview_kind(data):
    s, _ = session()
    assert s.handle(data) is None and s.dropped == 1 and s.previews == 0


def test_binary_drops_oversized_and_failing_pipeline_messages():
    s, _ = session(pipeline=SpyPipeline(fail=True), max_message_bytes=100)
    assert s.handle(bytes([KIND_PREVIEW]) + b"\xff\xd8" + b"\x00" * 200) is None  # too big
    assert s.handle(bytes([KIND_PREVIEW]) + b"\xff\xd8jpeg") is None  # the pipeline raised
    assert s.dropped == 2 and s.previews == 0


def test_preview_jpeg_serves_video_and_snapshots(tmp_path):
    c, p = remote_client(tmp_path, video={"fps": 50})
    jpeg = phone_jpeg()
    with c, c.websocket_connect("/ingest-events") as ws:
        ws.send_text(hello_msg(phone_time=time.time()))
        assert ws.receive_json()["type"] == "hello_ack"
        ws.send_bytes(bytes([KIND_PREVIEW]) + jpeg)
        assert wait_for(lambda: p.latest_frame_jpeg() == jpeg)
        assert jpeg in c.get("/video?max_frames=1").content


# -- RemotePipeline ---------------------------------------------------------------------------
def test_build_pipeline_selects_remote():
    assert isinstance(build_pipeline(stub_cfg(remote=True)), RemotePipeline)
    assert isinstance(build_pipeline(stub_cfg(remote=False)), MockPipeline)


def test_remote_pipeline_implements_the_pipeline_interface():
    p = RemotePipeline(stub_cfg())
    for name in PIPELINE_METHODS:
        assert callable(getattr(p, name)), name
    assert p.status()["source"] == "remote"


async def test_run_awaits_until_stop_without_models_or_watchdog():
    p = RemotePipeline(stub_cfg())
    task = asyncio.create_task(p.run(None, None, None))
    await asyncio.sleep(0.01)
    assert not task.done() and p.status()["state"] == "running"
    p.stop()
    await asyncio.wait_for(task, 1.0)
    assert p.status()["state"] == "stopped"


async def test_mark_treat_pushes_a_downlink_and_push_config_pushes_the_rules():
    cfg = stub_cfg()
    cfg["data"] = {"rules": {"min_score": 0.3}}
    p = RemotePipeline(cfg)
    sent = []

    async def send(msg):
        sent.append(msg)

    p.attach(None, send)
    p.mark_treat(123.0)
    p.push_config()  # queued after the treat: downlinks keep their order
    await asyncio.sleep(0.02)
    # no phone session attached -> offset 0, so the ts passes through unchanged
    assert sent == [{"type": "treat", "data": {"ts": 123.0, "offset_s": 0.0}},
                    {"type": "config", "data": {"rules": {"min_score": 0.3}}}]
    p.mark_treat(124.0)
    await asyncio.sleep(0.02)
    assert sent[-1] == {"type": "treat", "data": {"ts": 124.0, "offset_s": 0.0}}
    assert p.status()["downlinks_sent"] == 3


def test_downlinks_without_a_phone_are_counted_not_queued():
    p = RemotePipeline(stub_cfg())
    p.mark_treat(1.0)
    assert p.status()["downlinks_dropped"] == 1


async def test_a_failing_sender_does_not_raise_and_drains_the_queue():
    p = RemotePipeline(stub_cfg())

    async def send(msg):
        raise RuntimeError("socket is closed")

    p.attach(None, send)
    p.mark_treat(1.0)
    p.mark_treat(2.0)
    await asyncio.sleep(0.02)
    st = p.status()
    assert st["downlinks_sent"] == 0 and st["downlinks_dropped"] == 2
    p.detach(None)
    assert p._session is None


def test_status_is_stalled_after_camera_silence_and_mirrors_phone_metrics():
    cfg = stub_cfg()
    cfg["web"]["remote"] = {"stale_s": 2.0}
    clock = Clock()
    p = RemotePipeline(cfg, clock=clock)
    s = RemoteSession(parse_remote_hello(hello_msg()), p, SpyRuntime(), clock=clock)
    p.attach(s, None)
    assert p.status()["state"] == "running" and p.status()["last_frame_age_s"] is None
    for i in range(2):  # 8 events/s and 8 previews/s
        s.handle_text(envelope("frame", frame_data(PHONE_T + i * 0.125)))
        s.handle(bytes([KIND_PREVIEW]) + phone_jpeg())
        clock.t += 0.125
    st = p.status()
    assert st["state"] == "running" and st["fps"] == 8.0 and st["preview_fps"] == 8.0
    assert st["last_frame_age_s"] == 0.125 and st["audio_ok"] is False
    assert st["device"] == "Pixel 7a" and st["clock_offset_s"] == 4000.0 and st["clock_samples"] == 1
    clock.t += 2.5
    assert p.status()["state"] == "stalled"
    assert p.latest_frame_jpeg() is not None  # the last preview is kept for the dashboard


def test_bind_pipeline_is_a_no_op_for_pipelines_without_attach():
    s, _ = session()
    assert bind_pipeline(SpyPipeline(), s, None) is False


# -- Step 1.5: drops and close codes ----------------------------------------------------------
def test_invalid_envelopes_are_counted_and_the_socket_stays_open(tmp_path):
    c, _ = remote_client(tmp_path)
    now = time.time()
    with c, c.websocket_connect("/ingest-events") as ws:
        ws.send_text(hello_msg(phone_time=now))
        assert ws.receive_json()["type"] == "hello_ack"
        for bad in ("not json", json.dumps({"type": "frame"}), json.dumps({"type": "wat", "data": {}}),
                    envelope("frame", frame_data(now, extra=1)), envelope("rules", rules_data(now, "sad"))):
            ws.send_text(bad)
        ws.send_bytes(b"\x07nope")
        ws.send_text(envelope("audio", audio_data(now)))
        assert wait_for(lambda: c.get("/status").json()["phone"]["events"] == 1)
        phone = c.get("/status").json()["phone"]
    assert phone["dropped"] == 6 and phone["connected"] is True
    assert phone["device"] == "Pixel 7a" and phone["transport"] == "ingest-events"


def test_bad_hello_closes_4400(tmp_path):
    c, _ = remote_client(tmp_path)
    with c, c.websocket_connect("/ingest-events") as ws:
        ws.send_text(json.dumps({"type": "hello", "device": "Pixel 7a"}))
        with pytest.raises(WebSocketDisconnect) as exc:
            ws.receive_text()
    assert exc.value.code == 4400 and "hello" in exc.value.reason


def test_binary_before_hello_closes_4400(tmp_path):
    c, _ = remote_client(tmp_path)
    with c, c.websocket_connect("/ingest-events") as ws:
        ws.send_bytes(bytes([KIND_PREVIEW]) + phone_jpeg())
        with pytest.raises(WebSocketDisconnect) as exc:
            ws.receive_text()
    assert exc.value.code == 4400


def test_second_phone_is_rejected_4409_and_the_first_keeps_streaming(tmp_path):
    c, _ = remote_client(tmp_path)
    now = time.time()
    with c, c.websocket_connect("/ingest-events") as first:
        first.send_text(hello_msg(phone_time=now))
        assert first.receive_json()["type"] == "hello_ack"
        with c.websocket_connect("/ingest-events") as second:
            second.send_text(hello_msg(device="iPhone", phone_time=now))
            with pytest.raises(WebSocketDisconnect) as exc:
                second.receive_text()
        assert exc.value.code == 4409 and exc.value.reason == "Another phone is already streaming"
        first.send_text(envelope("audio", audio_data(now)))
        assert wait_for(lambda: c.get("/status").json()["phone"]["events"] == 1)
        assert c.get("/status").json()["phone"]["device"] == "Pixel 7a"


def test_a_quiet_phone_is_replaced_with_4408(tmp_path):
    c, _ = remote_client(tmp_path, ingest={"stale_s": 0.2})
    now = time.time()
    with c, c.websocket_connect("/ingest-events") as first:
        first.send_text(hello_msg(phone_time=now))
        assert first.receive_json()["type"] == "hello_ack"
        time.sleep(0.35)
        with c.websocket_connect("/ingest-events") as second:
            second.send_text(hello_msg(device="iPhone", phone_time=now))
            assert second.receive_json()["type"] == "hello_ack"
            with pytest.raises(WebSocketDisconnect) as exc:
                first.receive_text()
            assert exc.value.code == 4408
            assert wait_for(lambda: (c.get("/status").json()["phone"] or {}).get("device") == "iPhone")


def test_browser_phone_holds_the_single_phone_slot(tmp_path):
    c, _ = remote_client(tmp_path)
    with c, c.websocket_connect("/ingest") as browser:
        browser.send_text(json.dumps({"type": "hello", "width": 360, "height": 640, "fps": 8,
                                      "sample_rate": 48000, "device": "Chrome"}))
        assert wait_for(lambda: c.get("/status").json()["phone"] is not None)
        with c.websocket_connect("/ingest-events") as android:
            android.send_text(hello_msg(phone_time=time.time()))
            with pytest.raises(WebSocketDisconnect) as exc:
                android.receive_text()
        assert exc.value.code == 4409
        assert c.get("/status").json()["phone"]["device"] == "Chrome"


# -- Step 1.6: the dashboard's phone card -----------------------------------------------------
def test_dashboard_sees_the_phone_connect_and_disconnect(tmp_path):
    c, _ = remote_client(tmp_path)
    with c, c.websocket_connect("/ws") as dash:
        assert dash.receive_json()["type"] == "status"
        with c.websocket_connect("/ingest-events") as ws:
            ws.send_text(hello_msg(phone_time=time.time()))
            assert ws.receive_json()["type"] == "hello_ack"
            connected = next_phone(dash, lambda p: p["connected"])
            ws.send_text(envelope("audio", audio_data(time.time())))
            assert wait_for(lambda: c.get("/status").json()["phone"]["events"] == 1)
            ws.close(1001)  # the app was killed (its own Stop button sends 1000)
        dropped = next_phone(dash, lambda p: not p["connected"])
    assert (connected["device"], connected["transport"]) == ("Pixel 7a", "ingest-events")
    assert connected["facing"] is None and connected["camera"] is True
    assert dropped["connected"] is False and dropped["events"] == 1  # the card keeps the last counters
    assert c.get("/status").json()["phone"]["connected"] is False


def test_the_stop_button_forgets_the_phone(tmp_path):
    c, _ = remote_client(tmp_path)
    with c:
        with c.websocket_connect("/ingest-events") as ws:
            ws.send_text(hello_msg(phone_time=time.time()))
            assert ws.receive_json()["type"] == "hello_ack"
            assert wait_for(lambda: c.get("/status").json()["phone"] is not None)
            ws.close(1000)
        assert wait_for(lambda: c.get("/status").json()["phone"] is None)


# -- Step 1.7: silence -> stalled -> unknown ---------------------------------------------------
def test_silence_marks_the_pipeline_stalled_and_the_label_unknown(tmp_path):
    c, _ = remote_client(tmp_path, remote={"stale_s": 0.3},
                         state={"no_dog_unknown_s": 1.0, "persist_s": 0.1})
    now = time.time()
    with c, c.websocket_connect("/ingest-events") as ws:
        ws.send_text(hello_msg(phone_time=now))
        assert ws.receive_json()["type"] == "hello_ack"
        ws.send_text(envelope("frame", frame_data(now)))
        ws.send_text(envelope("rules", rules_data(now, "happy")))
        assert wait_for(lambda: c.get("/status").json()["pipeline_status"]["events"] == 2)
        assert wait_for(lambda: "happy" in [e["data"]["emotion"] for e in emotion_events(c)])
        # the phone goes quiet: the source stalls and the no-dog path falls back to unknown
        assert wait_for(lambda: c.get("/status").json()["pipeline_status"]["state"] == "stalled")
        assert wait_for(lambda: any(e["data"]["reason"].startswith("No dog visible")
                                    for e in emotion_events(c)))


# -- treat downlink ---------------------------------------------------------------------------
def test_the_treat_button_reaches_the_phone(tmp_path):
    c, _ = remote_client(tmp_path)
    with c, c.websocket_connect("/ingest-events") as ws:
        ws.send_text(hello_msg(phone_time=time.time()))
        assert ws.receive_json()["type"] == "hello_ack"
        c.post("/treat")
        down = ws.receive_json()
    assert down["type"] == "treat" and abs(down["data"]["ts"] - time.time()) < 5.0


def test_a_treat_sent_by_the_phone_comes_back_down_the_same_socket(tmp_path):
    c, _ = remote_client(tmp_path)
    now = time.time()
    with c, c.websocket_connect("/ingest-events") as ws:
        ws.send_text(hello_msg(phone_time=now))
        assert ws.receive_json()["type"] == "hello_ack"
        ws.send_text(envelope("treat", {"ts": now}))
        down = ws.receive_json()
    # the phone's clock is ~now here, so the downlink ts is the ts it sent
    assert down["type"] == "treat" and abs(down["data"]["ts"] - now) < 1.0
    assert any(m["type"] == "treat" for m in c.get("/events").json()["events"])


# -- config downlink --------------------------------------------------------------------------
def test_phone_config_pushes_the_servers_rules_to_the_phone(tmp_path):
    c, p = remote_client(tmp_path)
    p.cfg["data"] = {"rules": {"min_score": 0.3, "treat_window_s": 10.0}}
    with c, c.websocket_connect("/ingest-events") as ws:
        ws.send_text(hello_msg(phone_time=time.time()))
        assert ws.receive_json()["type"] == "hello_ack"
        assert c.post("/phone/config", json={"rules": {"min_score": 0.42}}).json()["sent"] is True
        assert ws.receive_json() == {"type": "config", "data": {"rules": {"min_score": 0.42}}}
        assert c.post("/phone/config", json={}).json()["sent"] is True
        assert ws.receive_json()["data"] == {"rules": {"min_score": 0.3, "treat_window_s": 10.0}}
        assert c.post("/phone/config", json={"rules": 3}).status_code == 400


def test_phone_config_needs_the_remote_pipeline(tmp_path):
    cfg = cfg_for(tmp_path, pipeline="mock")
    app = create_app(cfg, pipeline=MockPipeline(cfg), interpreter=NoLLM(), notifier=DashboardOnlyNotifier())
    with TestClient(app) as c:
        r = c.post("/phone/config", json={"rules": {}})
    assert r.status_code == 400 and "remote" in r.json()["detail"]


def test_ingest_events_warns_when_the_server_pipeline_is_not_remote(tmp_path, caplog):
    cfg = cfg_for(tmp_path, pipeline="mock")
    app = create_app(cfg, pipeline=MockPipeline(cfg), interpreter=NoLLM(), notifier=DashboardOnlyNotifier())
    with caplog.at_level(logging.WARNING, logger="ingest-events"), TestClient(app) as c:
        with c.websocket_connect("/ingest-events") as ws:
            ws.send_text(hello_msg(phone_time=time.time()))
            assert ws.receive_json()["type"] == "hello_ack"
    assert any("WEB_PIPELINE=remote" in r.getMessage() for r in caplog.records)


# -- fix round 1: review findings ---------------------------------------------------------------
def test_runtime_phone_open_annotations_resolve():
    """Finding 1: RemoteHello is used in the annotation, so it has to be imported (get_type_hints
    evaluates the strings that `from __future__ import annotations` leaves behind)."""
    hints = typing.get_type_hints(Runtime.phone_open)
    assert RemoteHello in hints["hello"].__args__
    assert RemoteSession in hints["session"].__args__


@pytest.mark.parametrize("kind,data", [("frame", frame_data(PHONE_T)), ("audio", audio_data(PHONE_T)),
                                        ("rules", rules_data(PHONE_T)), ("treat", {"ts": PHONE_T})])
def test_a_raising_runtime_costs_one_envelope_not_the_socket(kind, data):
    """Finding 2: the dispatch is guarded, and the counters only move once it succeeded."""
    s, clock = session(runtime=BoomRuntime())
    assert s.handle_text(envelope(kind, data)) is None
    assert s.dropped == 1 and s.events == 0 and s.treats == 0
    assert s.snapshot(clock.t)["dropped"] == 1


def test_a_raising_runtime_keeps_the_socket_open(tmp_path):
    c, _ = remote_client(tmp_path)
    with c:
        rt = c.app.state.rt
        boom = BoomRuntime().exc
        rt.on_frame, rt.on_audio, rt.on_rules, rt.treat = (lambda *a, **k: (_ for _ in ()).throw(boom)
                                                           for _ in range(4))
        now = time.time()
        with c.websocket_connect("/ingest-events") as ws:
            ws.send_text(hello_msg(phone_time=now))
            assert ws.receive_json()["type"] == "hello_ack"
            for kind, data in (("frame", frame_data(now)), ("audio", audio_data(now)),
                               ("rules", rules_data(now)), ("treat", {"ts": now})):
                ws.send_text(envelope(kind, data))
            ws.send_bytes(bytes([KIND_PREVIEW]) + phone_jpeg())
            assert wait_for(lambda: c.get("/status").json()["phone"]["dropped"] == 4)
            phone = c.get("/status").json()["phone"]
            assert (phone["events"], phone["treats"], phone["previews"]) == (0, 0, 1)
            assert phone["connected"] is True
            # the socket survived, so the next envelope is served again
            rt.on_audio = lambda ev: None
            ws.send_text(envelope("audio", audio_data(now)))
            assert wait_for(lambda: c.get("/status").json()["phone"]["events"] == 1)


async def test_a_stuck_sender_times_out_and_is_counted():
    """Finding 3: a half-open socket cannot wedge the downlink queue for ever."""
    cfg = stub_cfg()
    cfg["web"]["remote"] = {"downlink_timeout_s": 0.05}
    p = RemotePipeline(cfg)

    async def stuck(msg):
        await asyncio.sleep(10)

    p.attach(None, stuck)
    p.mark_treat(1.0)
    await asyncio.sleep(0.2)
    st = p.status()
    assert st["downlinks_sent"] == 0 and st["downlinks_dropped"] == 1
    assert p._flushing is False and not p._queue

    sent = []

    async def ok(msg):
        sent.append(msg)

    p.attach(None, ok)
    p.mark_treat(2.0)
    await asyncio.sleep(0.05)
    assert [m["type"] for m in sent] == ["treat"]


async def test_a_stuck_flush_is_logged_and_not_repeated(caplog):
    cfg = stub_cfg()
    cfg["web"]["remote"] = {"downlink_timeout_s": 5.0, "ping_every_s": 0.05}
    p = RemotePipeline(cfg)

    async def stuck(msg):
        await asyncio.sleep(10)

    p.attach(None, stuck)
    with caplog.at_level(logging.WARNING, logger="remote-pipeline"):
        p.mark_treat(1.0)
        await asyncio.sleep(0.15)
        assert p._flushing is True
        p.status()  # any status poll is what notices a flush that has been stuck too long
        warned = [r for r in caplog.records if "stuck" in r.getMessage()]
        p.status()
        p.status()
    assert len(warned) == 1
    assert len([r for r in caplog.records if "stuck" in r.getMessage()]) == 1  # not spammed


async def test_the_treat_downlink_is_stamped_in_the_phone_clock():
    """Finding 4: the app compares the treat against its own clock, so the downlink must speak it."""
    clock = Clock()
    cfg = stub_cfg()
    p = RemotePipeline(cfg, clock=clock)
    s = RemoteSession(parse_remote_hello(hello_msg()), p, SpyRuntime(), clock=clock)  # 4000 s behind
    sent = []

    async def send(msg):
        sent.append(msg)

    p.attach(s, send)
    p.mark_treat(clock.t)  # what Runtime.treat() does: a server-clock ts
    p.mark_treat(clock.t + 30.0)
    await asyncio.sleep(0.02)
    assert sent == [{"type": "treat", "data": {"ts": clock.t - 4000.0, "offset_s": 4000.0}},
                    {"type": "treat", "data": {"ts": clock.t - 3970.0, "offset_s": 4000.0}}]
    assert s.metrics(clock.t)["clock_offset_s"] == 4000.0


def test_the_treat_downlink_reaches_a_phone_an_hour_behind_in_its_own_clock(tmp_path):
    c, _ = remote_client(tmp_path)
    skew = 3600.0
    with c, c.websocket_connect("/ingest-events") as ws:
        phone_now = time.time() - skew
        ws.send_text(hello_msg(phone_time=phone_now))
        ack = ws.receive_json()
        offset = ack["server_time"] - phone_now
        server_t = c.post("/treat").json()["ts"]
        down = ws.receive_json()
    assert down["type"] == "treat"
    assert down["data"]["offset_s"] == pytest.approx(offset, abs=0.5)
    assert down["data"]["ts"] == pytest.approx(server_t - offset, abs=0.5)
    assert phone_now - 5.0 < down["data"]["ts"] < time.time() - skew  # plausible on the phone's clock


def test_a_treat_the_phone_sent_comes_back_in_the_phone_clock(tmp_path):
    c, _ = remote_client(tmp_path)
    phone_now = time.time() - 3600.0
    with c, c.websocket_connect("/ingest-events") as ws:
        ws.send_text(hello_msg(phone_time=phone_now))
        assert ws.receive_json()["type"] == "hello_ack"
        ws.send_text(envelope("treat", {"ts": phone_now + 2.0}))
        echo = ws.receive_json()
    assert echo["data"]["ts"] == pytest.approx(phone_now + 2.0, abs=0.5)


def test_an_implausible_ts_is_dropped():
    """Finding 5: a ts that rebases to the far future or past would freeze every server timer."""
    s, clock = session(max_skew_s=60.0)
    assert s.handle_text(envelope("frame", frame_data(PHONE_T + 30.0))) == "frame"
    assert s.handle_text(envelope("frame", frame_data(4.1e9))) is None       # year 2100
    assert s.handle_text(envelope("frame", frame_data(PHONE_T - 3600.0))) is None  # an hour stale
    assert s.handle_text(envelope("treat", {"ts": 4.1e9})) is None
    assert s.dropped == 3 and s.frames == 1 and s.treats == 0


def test_an_envelope_from_the_year_2100_never_freezes_the_clock(tmp_path):
    c, p = remote_client(tmp_path)
    now = time.time()
    with c, c.websocket_connect("/ingest-events") as ws:
        ws.send_text(hello_msg(phone_time=now))
        assert ws.receive_json()["type"] == "hello_ack"
        ws.send_text(envelope("frame", frame_data(4.1e9)))
        ws.send_text(envelope("frame", frame_data(now)))
        assert wait_for(lambda: c.get("/status").json()["phone"]["events"] == 1)
        st = c.get("/status").json()
    assert st["phone"]["dropped"] == 1 and st["phone"]["connected"] is True
    assert st["pipeline_status"]["last_frame_age_s"] >= 0.0
    assert st["pipeline_status"]["state"] == "running"
