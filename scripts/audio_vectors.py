"""Generate tests/vectors/audio_v1.json: the shared IMA-ADPCM test vectors for the Python, TypeScript
and Kotlin codecs. The Python implementation in backend/web/audio_codec.py is the reference.

    python scripts/audio_vectors.py            # writes tests/vectors/audio_v1.json
    python scripts/audio_vectors.py --out DIR  # writes elsewhere (used by tests)

Deterministic: fixed seed, integer-only encoding. Re-running must reproduce the committed file byte for
byte; tests/web/test_audio_codec.py fails if it does not. Android keeps a copy under
android/app/src/test/resources/audio/ which tests/web/test_audio_codec.py also keeps identical.
"""

from __future__ import annotations

import argparse
import json
import math
import random
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from backend.web import audio_codec as ac  # noqa: E402

SEED = 20261011
DEFAULT_OUT = ROOT / "tests" / "vectors" / "audio_v1.json"
ANDROID_COPY = ROOT / "android" / "app" / "src" / "test" / "resources" / "audio" / "audio_v1.json"

# The error bound is part of the spec: the reference decoder must stay within it (checked here).
BOUND_SYNTHETIC = 8192  # 12000-amplitude sweep, full-band noise burst (slope-limited peak 6313) and silence
BOUND_QUIET = 512
BOUND_ZEROS = 0
BOUND_SQUARE = 65535  # stress vector: a full-scale square wave cannot be followed; it checks clamping, not quality


def _clip(x: float) -> int:
    return max(ac.S16_MIN, min(ac.S16_MAX, int(round(x))))


def synthetic_mixed(rng: random.Random, amplitude: float) -> list[int]:
    """100 ms: a 200 Hz to 4 kHz sine sweep (first 60 ms), a noise burst (60 to 80 ms), silence after."""
    n = ac.CHUNK_SAMPLES
    rate = 16000.0
    out: list[int] = []
    phase = 0.0
    for i in range(n):
        t = i / rate
        if t < 0.060:
            freq = 200.0 + (4000.0 - 200.0) * (t / 0.060)
            phase += 2.0 * math.pi * freq / rate
            out.append(_clip(amplitude * math.sin(phase)))
        elif t < 0.080:
            out.append(_clip(rng.uniform(-amplitude, amplitude)))
        else:
            out.append(0)
    return out


def zeros() -> list[int]:
    return [0] * ac.CHUNK_SAMPLES


def square_full_scale() -> list[int]:
    """Full-scale square wave, period 40 samples (400 Hz at 16 kHz)."""
    return [ac.S16_MAX if (i // 20) % 2 == 0 else ac.S16_MIN for i in range(ac.CHUNK_SAMPLES)]


def _vector(name: str, samples: list[int], bound: int) -> dict:
    block = ac.encode_block(samples)
    decoded = ac.decode_block(block)
    err = max(abs(a - b) for a, b in zip(samples, decoded))
    if err > bound:
        raise SystemExit(f"{name}: reference max error {err} exceeds the bound {bound}")
    frame = ac.pack_frame(ac.CODEC_IMA_ADPCM, seq=7, t_ms=1234, payload=block)
    return {
        "name": name,
        "samples": samples,
        "block_hex": block.hex(),
        "decoded": decoded,
        "max_abs_error": err,
        "max_abs_error_bound": bound,
        "frame_hex": frame.hex(),
        "frame": {"seq": 7, "t_ms": 1234},
    }


def build() -> dict:
    rng = random.Random(SEED)
    vectors = [
        _vector("synthetic_mixed", synthetic_mixed(rng, 12000.0), BOUND_SYNTHETIC),
        _vector("synthetic_quiet", synthetic_mixed(rng, 300.0), BOUND_QUIET),
        _vector("zeros", zeros(), BOUND_ZEROS),
        _vector("square_full_scale", square_full_scale(), BOUND_SQUARE),
    ]
    reject_encode = []
    for name, n in (("encode_too_short_3", 3), ("encode_odd_1599", 1599)):
        reject_encode.append({"name": name, "samples": [0] * n})
    reject_decode = []
    good = bytes.fromhex(vectors[0]["block_hex"])
    cases = {
        "decode_short_4": good[:4],
        "decode_odd_803": good[:803],
        "decode_long_805": good + b"\x00",
        "decode_step_index_89": good[:2] + bytes([89]) + good[3:],
        "decode_reserved_nonzero": good[:3] + b"\x01" + good[4:],
        "decode_padding_nibble_set": good[:-1] + bytes([good[-1] | 0x10]),
    }
    for name, block in cases.items():
        reject_decode.append({"name": name, "block_hex": block.hex()})
    return {
        "version": 1,
        "seed": SEED,
        "codec": ac.CODEC_IMA_ADPCM,
        "rate": 16000,
        "chunk_samples": ac.CHUNK_SAMPLES,
        "block_bytes": ac.ADPCM_BLOCK_BYTES,
        "initial_step_index": 0,
        "step_table": list(ac.STEP_TABLE),
        "index_table": list(ac.INDEX_TABLE),
        "vectors": vectors,
        "reject_encode": reject_encode,
        "reject_decode": reject_decode,
    }


def render(doc: dict) -> str:
    return json.dumps(doc, indent=1, sort_keys=False) + "\n"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    args = parser.parse_args()
    text = render(build())
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(text, encoding="utf-8")
    if args.out == DEFAULT_OUT:
        ANDROID_COPY.parent.mkdir(parents=True, exist_ok=True)
        ANDROID_COPY.write_text(text, encoding="utf-8")
    print(f"wrote {args.out}")


if __name__ == "__main__":
    main()
