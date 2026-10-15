"""AudioHub (T3.9, A3.2): bounded per-listener queues with drop-oldest, kick semantics and client count."""

import asyncio

from backend.web import audio_codec as ac
from backend.web.audio_hub import AUDIO_QUEUE_FRAMES, KICKED_AUDIO, AudioHub


def frame(seq: int) -> bytes:
    """A relayed frame: codec | seq | t_ms | payload (the 0x02 byte is already gone)."""
    return bytes([ac.CODEC_IMA_ADPCM]) + seq.to_bytes(4, "little") + (0).to_bytes(4, "little") + bytes(
        ac.ADPCM_BLOCK_BYTES)


def test_a_slow_listener_keeps_only_the_newest_frames():
    hub = AudioHub()
    q = hub.subscribe()
    for seq in range(AUDIO_QUEUE_FRAMES + 5):
        hub.publish(frame(seq))
    assert q.qsize() == AUDIO_QUEUE_FRAMES  # bounded: memory does not grow with the backlog
    kept = [int.from_bytes(q.get_nowait()[1:5], "little") for _ in range(AUDIO_QUEUE_FRAMES)]
    assert kept == list(range(5, AUDIO_QUEUE_FRAMES + 5))  # oldest five were dropped, order kept


def test_a_slow_listener_does_not_hold_back_a_fast_one():
    hub = AudioHub(maxlen=3)
    slow, fast = hub.subscribe(), hub.subscribe()
    for seq in range(10):
        hub.publish(frame(seq))
        fast.get_nowait()  # the fast listener drains every frame
    assert slow.qsize() == 3
    assert fast.qsize() == 0


def test_publish_remembers_the_codec_and_handles_no_listeners():
    hub = AudioHub()
    assert hub.last_codec is None
    hub.publish(frame(1))  # nobody listening: a no-op, never an error
    assert hub.last_codec == ac.CODEC_IMA_ADPCM
    assert hub.client_count == 0


def test_subscribe_and_unsubscribe_count_listeners():
    hub = AudioHub()
    a = hub.subscribe()
    b = hub.subscribe(owner=True)
    assert hub.client_count == 2
    hub.unsubscribe(a)
    hub.unsubscribe(a)  # idempotent: a finally block may run twice
    assert hub.client_count == 1
    hub.unsubscribe(b)
    assert hub.client_count == 0


def test_kick_all_spares_owners_unless_asked_and_marks_the_queue():
    hub = AudioHub()
    viewer, owner = hub.subscribe(), hub.subscribe(owner=True)
    assert hub.kick_all() == 1
    assert hub.client_count == 1
    assert viewer.get_nowait() is KICKED_AUDIO
    assert owner.empty()
    assert hub.kick_all(include_owners=True) == 1
    assert hub.client_count == 0


def test_kick_on_a_full_queue_still_delivers_the_sentinel():
    hub = AudioHub(maxlen=2)
    q = hub.subscribe()
    hub.publish(frame(1))
    hub.publish(frame(2))
    hub.kick_all()
    assert q.qsize() == 2
    items = [q.get_nowait(), q.get_nowait()]
    assert items[-1] is KICKED_AUDIO


def test_queue_consumers_wake_up_on_publish():
    async def scenario():
        hub = AudioHub()
        q = hub.subscribe()
        hub.publish(frame(9))
        return await asyncio.wait_for(q.get(), timeout=1)

    assert asyncio.run(scenario())[1:5] == (9).to_bytes(4, "little")
