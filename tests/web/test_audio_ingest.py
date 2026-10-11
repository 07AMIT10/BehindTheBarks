"""Phone audio frames on the event sink (T3.9, A3.1): RemoteSession accepts well-formed 0x02 frames and
counts everything else. The server validates the frame and never decodes it.
"""

import time
from types import SimpleNamespace

import pytest

from backend.web import audio_codec as ac
from backend.web.ingest_events import MAX_AUDIO_FRAME_BYTES, RemoteHello, RemoteSession


class Pipe:
    """The two attributes the session reads from a RemotePipeline."""

    def __init__(self, effective_mode="full"):
        self.effective_mode = effective_mode


def session(pipe=None, runtime=None):
    pipe = pipe or Pipe()
    runtime = runtime or SimpleNamespace()
    return RemoteSession(RemoteHello(device="Pixel", phone_time=time.time()), pipe, runtime,
                         clock=time.time)


def pcm_frame(seq=1):
    return ac.pack_frame(ac.CODEC_PCM16, seq, 1234, bytes(ac.PCM16_PAYLOAD_BYTES))


def adpcm_frame(seq=1):
    return ac.pack_frame(ac.CODEC_IMA_ADPCM, seq, 1234, ac.encode_block([0] * ac.CHUNK_SAMPLES))


def test_valid_adpcm_and_pcm_frames_pass_validation_and_are_counted_as_policy_drops_without_listeners():
    # No audio hub and no listener: the frame is well formed but not allowed, so it is a policy drop.
    s = session()
    assert s.handle(adpcm_frame()) is None
    assert s.handle(pcm_frame()) is None
    assert s.audio_dropped_policy == 2
    assert s.audio_dropped_invalid == 0
    assert s.dropped == 0  # a policy drop is not a malformed message


@pytest.mark.parametrize("data", [
    bytes([0x02, 2]) + bytes(8) + bytes(ac.PCM16_PAYLOAD_BYTES),       # codec 2 (reserved)
    bytes([0x02, 7]) + bytes(8) + bytes(ac.ADPCM_BLOCK_BYTES),         # unknown codec
    bytes([0x02, 0]) + bytes(8) + bytes(ac.ADPCM_BLOCK_BYTES),         # PCM16 with ADPCM length
    bytes([0x02, 1]) + bytes(8) + bytes(ac.PCM16_PAYLOAD_BYTES),       # ADPCM with PCM16 length
    bytes([0x02, 1]) + bytes(8) + bytes(ac.ADPCM_BLOCK_BYTES - 1),     # short ADPCM block
    bytes([0x02, 0]) + bytes(8) + bytes(ac.PCM16_PAYLOAD_BYTES + 2),   # long PCM16 block
    bytes([0x02, 1]) + bytes(5),                                       # shorter than the header
    bytes([0x02, 1]) + bytes(8) + bytes(MAX_AUDIO_FRAME_BYTES),        # oversize
])
def test_bad_frames_are_dropped_and_counted_as_invalid(data):
    s = session(Pipe("full"))
    assert s.handle(data) is None
    assert s.audio_dropped_invalid == 1
    assert s.dropped == 1
    assert s.audio_dropped_policy == 0
    assert s.audio_frames == 0


def test_an_unknown_binary_kind_is_a_plain_drop_not_an_audio_frame():
    s = session(Pipe("full"))
    assert s.handle(bytes([0x03, 1]) + bytes(8) + bytes(ac.ADPCM_BLOCK_BYTES)) is None
    assert s.dropped == 1
    assert s.audio_dropped_invalid == 0 and s.audio_dropped_policy == 0


def test_mode_below_full_drops_a_valid_frame_and_counts_policy():
    for mode in ("blackout", "privacy", "limited"):
        s = session(Pipe(mode))
        s.handle(adpcm_frame())
        assert s.audio_dropped_policy == 1, mode
        assert s.audio_frames == 0


def test_status_and_metrics_expose_the_audio_counters():
    s = session(Pipe("privacy"))
    s.handle(adpcm_frame())
    s.handle(bytes([0x02, 9]) + bytes(20))
    now = time.time()
    for view in (s.metrics(now), s.snapshot(now)):
        assert view["audio_dropped_policy"] == 1
        assert view["audio_dropped_invalid"] == 1
        assert view["audio_frames"] == 0
        assert view["audio_bytes"] == 0
        assert view["audio_clients"] == 0


def test_preview_frames_still_work_next_to_audio():
    class Pipeline(Pipe):
        def __init__(self):
            super().__init__("full")
            self.jpegs = []

        def ingest_frame(self, jpeg, ts):
            self.jpegs.append(jpeg)

    p = Pipeline()
    s = session(p)
    assert s.handle(bytes([0x01]) + b"\xff\xd8abc") == "preview"
    assert s.handle(adpcm_frame()) is None
    assert p.jpegs == [b"\xff\xd8abc"]
