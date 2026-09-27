import json

import pytest

from backend.web.ingest import (KIND_AUDIO, KIND_FRAME, FpsMeter, Hello, HelloError, IngestSession,
                                parse_hello)

JPEG = b"\xff\xd8\xff\xe0fakejpeg\xff\xd9"


def hello_text(**over):
    msg = {"type": "hello", "width": 640, "height": 360, "fps": 8, "sample_rate": 48000, "device": "Pixel 7"}
    msg.update(over)
    return json.dumps(msg)


class Clock:
    def __init__(self, t=1000.0):
        self.t = t

    def __call__(self):
        return self.t


class SpyPipeline:
    def __init__(self, fail=False):
        self.frames, self.audio, self.fail = [], [], fail

    def ingest_frame(self, jpeg, ts):
        if self.fail:
            raise RuntimeError("boom")
        self.frames.append((jpeg, ts))

    def ingest_audio(self, pcm16, sample_rate, ts):
        self.audio.append((pcm16, sample_rate, ts))


def test_parse_minimal_hello_defaults():
    h = parse_hello(hello_text())
    assert h == Hello(width=640, height=360, fps=8.0, sample_rate=48000, device="Pixel 7", facing=None, camera=True)


def test_parse_optional_fields_and_trims_device():
    h = parse_hello(hello_text(facing="front", camera=False, device="  " + "x" * 100 + " "))
    assert h.facing == "front" and h.camera is False and h.device == "x" * 64


@pytest.mark.parametrize("text", [
    None, "", "not json", "[1, 2]", json.dumps({"type": "frame"}),
    hello_text(width="640"), hello_text(width=True), hello_text(height=-1), hello_text(fps=500),
    hello_text(sample_rate=1000), hello_text(device=""), hello_text(device=5),
    hello_text(facing="side"), hello_text(camera="no"),
])
def test_bad_hellos_raise(text):
    with pytest.raises(HelloError):
        parse_hello(text)


def test_mic_only_hello_allows_zero_video():
    h = parse_hello(hello_text(width=0, height=0, fps=0, camera=False))
    assert (h.width, h.height, h.fps, h.camera) == (0, 0, 0.0, False)


def test_fps_meter_sliding_window():
    m = FpsMeter(window_s=3.0)
    assert m.rate(0.0) == 0.0
    for i in range(9):  # 8 fps for 1 s
        m.add(10.0 + i * 0.125)
    assert m.rate(11.0) == 8.0
    assert m.rate(20.0) == 0.0  # everything aged out


def test_session_dispatches_by_kind_with_receipt_ts():
    clock, spy = Clock(), SpyPipeline()
    s = IngestSession(parse_hello(hello_text(sample_rate=44100)), spy, clock=clock)
    clock.t = 1001.0
    assert s.handle(bytes([KIND_FRAME]) + JPEG) == "frame"
    clock.t = 1001.5
    assert s.handle(bytes([KIND_AUDIO]) + b"\x01\x00\x02\x00") == "audio"
    assert spy.frames == [(JPEG, 1001.0)]
    assert spy.audio == [(b"\x01\x00\x02\x00", 44100, 1001.5)]
    assert (s.frames, s.audio_chunks, s.dropped, s.last_frame_ts, s.last_msg_ts) == (1, 1, 0, 1001.0, 1001.5)


def test_session_drops_unknown_empty_oversized_and_failing():
    clock = Clock()
    s = IngestSession(parse_hello(hello_text()), SpyPipeline(fail=True), clock=clock, max_message_bytes=100)
    assert s.handle(b"") is None
    assert s.handle(b"\x01") is None           # kind byte only
    assert s.handle(b"\x07abc") is None        # unknown kind
    assert s.handle(b"\x02" + b"\x00" * 200) is None  # too big
    assert s.handle(b"\x01" + JPEG) is None    # pipeline raised
    assert s.dropped == 5 and s.frames == 0


def test_snapshot_reports_fps_age_and_disconnect():
    clock = Clock()
    s = IngestSession(parse_hello(hello_text(facing="back")), SpyPipeline(), clock=clock)
    assert s.snapshot(clock.t)["last_frame_age_s"] is None
    for i in range(9):
        clock.t = 1000.0 + i * 0.125
        s.handle(bytes([KIND_FRAME]) + JPEG)
    snap = s.snapshot(1001.5)
    assert snap == {"connected": True, "device": "Pixel 7", "facing": "back", "camera": True, "fps": 8.0,
                    "width": 640, "height": 360, "sample_rate": 48000, "last_frame_age_s": 0.5}
    s.connected = False
    assert s.snapshot(1002.0)["fps"] == 0.0 and s.snapshot(1002.0)["connected"] is False
    assert s.idle_s(1003.0) == 2.0
    assert s.handle(bytes([KIND_FRAME]) + JPEG) is None  # a disconnected session ignores late data
