"""IMA-ADPCM block codec and the 0x02 audio frame header (see docs/AUDIO_WIRE.md).

This module is the reference implementation. scripts/audio_vectors.py generates tests/vectors/audio_v1.json
with it, and the Kotlin and TypeScript codecs must reproduce those vectors exactly.

Block (codec 1): predictor s16 LE | step_index u8 (0..88) | 0x00 | 800 bytes of nibbles.
The 1600-sample chunk's first sample is the predictor; samples 2..1600 are 4-bit codes, low nibble first.
The final high nibble is zero padding.
"""

from __future__ import annotations

import struct
from dataclasses import dataclass

FRAME_MAGIC = 0x02
HEADER_BYTES = 10  # magic u8 | codec u8 | seq u32 LE | t_ms u32 LE
CODEC_PCM16 = 0
CODEC_IMA_ADPCM = 1
CODEC_OPUS_RESERVED = 2

CHUNK_SAMPLES = 1600  # 100 ms at 16 kHz
PCM16_PAYLOAD_BYTES = CHUNK_SAMPLES * 2  # 3200
ADPCM_BLOCK_HEADER_BYTES = 4
ADPCM_NIBBLE_BYTES = CHUNK_SAMPLES // 2  # 1599 codes + 1 zero pad nibble = 1600 nibbles = 800 bytes
ADPCM_BLOCK_BYTES = ADPCM_BLOCK_HEADER_BYTES + ADPCM_NIBBLE_BYTES  # 804

S16_MIN, S16_MAX = -32768, 32767
U32_MAX = 0xFFFFFFFF

STEP_TABLE: tuple[int, ...] = (
    7, 8, 9, 10, 11, 12, 13, 14, 16, 17, 19, 21, 23, 25, 28, 31, 34, 37, 41, 45,
    50, 55, 60, 66, 73, 80, 88, 97, 107, 118, 130, 143, 157, 173, 190, 209, 230,
    253, 279, 307, 337, 371, 408, 449, 494, 544, 598, 658, 724, 796, 876, 963,
    1060, 1166, 1282, 1411, 1552, 1707, 1878, 2066, 2272, 2499, 2749, 3024, 3327,
    3660, 4026, 4428, 4871, 5358, 5894, 6484, 7132, 7845, 8630, 9493, 10442,
    11487, 12635, 13899, 15289, 16818, 18500, 20350, 22385, 24623, 27086, 29794,
    32767,
)
INDEX_TABLE: tuple[int, ...] = (-1, -1, -1, -1, 2, 4, 6, 8, -1, -1, -1, -1, 2, 4, 6, 8)
MAX_STEP_INDEX = len(STEP_TABLE) - 1  # 88

assert len(STEP_TABLE) == 89 and len(INDEX_TABLE) == 16


@dataclass(frozen=True)
class Frame:
    codec: int
    seq: int
    t_ms: int
    payload: bytes


def _clamp_predictor(value: int) -> int:
    return S16_MIN if value < S16_MIN else S16_MAX if value > S16_MAX else value


def _clamp_index(value: int) -> int:
    return 0 if value < 0 else MAX_STEP_INDEX if value > MAX_STEP_INDEX else value


def _step(predictor: int, index: int, code: int) -> tuple[int, int]:
    """Apply one 4-bit code: the decoder's update, shared with the encoder so both stay in lockstep."""
    step = STEP_TABLE[index]
    diff = step >> 3
    if code & 4:
        diff += step
    if code & 2:
        diff += step >> 1
    if code & 1:
        diff += step >> 2
    predictor = _clamp_predictor(predictor - diff if code & 8 else predictor + diff)
    return predictor, _clamp_index(index + INDEX_TABLE[code])


def encode_block(samples: list[int] | tuple[int, ...]) -> bytes:
    """Encode exactly CHUNK_SAMPLES s16 samples into one 804-byte ADPCM block (initial step index 0)."""
    if len(samples) != CHUNK_SAMPLES:
        raise ValueError(f"a chunk is {CHUNK_SAMPLES} samples, got {len(samples)}")
    for s in samples:
        if not isinstance(s, int) or isinstance(s, bool) or not S16_MIN <= s <= S16_MAX:
            raise ValueError(f"sample out of s16 range: {s!r}")
    predictor, index = samples[0], 0
    codes: list[int] = []
    for sample in samples[1:]:
        step = STEP_TABLE[index]
        diff = sample - predictor
        code = 0
        if diff < 0:
            code = 8
            diff = -diff
        if diff >= step:
            code |= 4
            diff -= step
        if diff >= step >> 1:
            code |= 2
            diff -= step >> 1
        if diff >= step >> 2:
            code |= 1
        predictor, index = _step(predictor, index, code)
        codes.append(code)
    if len(codes) % 2:
        codes.append(0)  # padding nibble
    packed = bytes(codes[i] | (codes[i + 1] << 4) for i in range(0, len(codes), 2))
    return struct.pack("<hBB", samples[0], 0, 0) + packed


def decode_block(block: bytes | bytearray | memoryview) -> list[int]:
    """Decode one 804-byte block to CHUNK_SAMPLES samples. Raises ValueError on any malformed block."""
    data = bytes(block)
    if len(data) != ADPCM_BLOCK_BYTES:
        raise ValueError(f"ADPCM block must be {ADPCM_BLOCK_BYTES} bytes, got {len(data)}")
    predictor, index, reserved = struct.unpack_from("<hBB", data, 0)
    if index > MAX_STEP_INDEX:
        raise ValueError(f"step index {index} out of range 0..{MAX_STEP_INDEX}")
    if reserved != 0:
        raise ValueError("reserved header byte must be 0")
    if data[-1] >> 4 != 0:
        raise ValueError("padding nibble must be 0")
    out = [predictor]
    for n in range(CHUNK_SAMPLES - 1):
        byte = data[ADPCM_BLOCK_HEADER_BYTES + n // 2]
        code = byte & 0x0F if n % 2 == 0 else byte >> 4
        predictor, index = _step(predictor, index, code)
        out.append(predictor)
    return out


def pack_frame(codec: int, seq: int, t_ms: int, payload: bytes) -> bytes:
    """Build a phone-to-server frame: 0x02 | codec u8 | seq u32 LE | t_ms u32 LE | payload."""
    if not 0 <= seq <= U32_MAX or not 0 <= t_ms <= U32_MAX:
        raise ValueError("seq and t_ms are u32")
    _check_payload(codec, payload)
    return bytes([FRAME_MAGIC, codec]) + struct.pack("<II", seq, t_ms) + bytes(payload)


def parse_frame(data: bytes | bytearray | memoryview) -> Frame:
    """Parse and validate a 0x02 frame. Raises ValueError for a wrong magic, unknown or reserved codec,
    or a payload whose length does not match the codec."""
    raw = bytes(data)
    if len(raw) < HEADER_BYTES:
        raise ValueError("frame shorter than the 10-byte header")
    if raw[0] != FRAME_MAGIC:
        raise ValueError(f"bad frame magic 0x{raw[0]:02x}")
    codec = raw[1]
    seq, t_ms = struct.unpack_from("<II", raw, 2)
    payload = raw[HEADER_BYTES:]
    _check_payload(codec, payload)
    return Frame(codec=codec, seq=seq, t_ms=t_ms, payload=payload)


def _check_payload(codec: int, payload: bytes) -> None:
    if codec == CODEC_PCM16:
        expected = PCM16_PAYLOAD_BYTES
    elif codec == CODEC_IMA_ADPCM:
        expected = ADPCM_BLOCK_BYTES
    else:
        raise ValueError(f"codec {codec} is not supported (0 PCM16, 1 IMA-ADPCM; 2 is reserved)")
    if len(payload) != expected:
        raise ValueError(f"codec {codec} payload must be {expected} bytes, got {len(payload)}")
