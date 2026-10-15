# Audio wire format and IMA-ADPCM codec (v1)

Status: v1, implemented in `backend/web/audio_codec.py` (reference), `frontend/lib/adpcm.ts` (decoder) and
`android/app/src/main/java/com/btb/ondevice/audio/AdpcmCodec.kt` (encoder and test decoder). The
shared vectors are `tests/vectors/audio_v1.json`, generated only by `scripts/audio_vectors.py`. Every
implementation must reproduce those vectors exactly. Change nothing here without bumping the version
and regenerating the vectors.

## 1. Phone to server: the audio frame

Sent as a binary WebSocket message on `/ingest-events`, one frame per 100 ms chunk.

```
0x02 | codec u8 | seq u32 LE | t_ms u32 LE | payload
```

- `0x02`: frame kind (audio). The server drops anything else on this path.
- `codec`: `0` PCM16, `1` IMA-ADPCM, `2` Opus (reserved, not implemented; a frame with codec 2 is rejected).
- `seq`: u32, increments by one per frame (wraps at 2^32). Gaps are loss, visible to the viewer only as a counter.
- `t_ms`: u32, phone monotonic milliseconds at the chunk start (wraps; used for latency, not ordering).
- `payload`: the codec block (sections 2 and 3). The payload length is fixed per codec; any other length is
  rejected.

| codec | name | payload bytes | samples per chunk |
|---|---|---|---|
| 0 | PCM16 (s16 LE, mono) | 3200 | 1600 |
| 1 | IMA-ADPCM block | 804 | 1600 |
| 2 | Opus (reserved) | n/a | n/a |

Total frame size: 3210 bytes for codec 0, 814 bytes for codec 1. Rate 16 kHz, 100 ms chunks, so
10 frames per second per stream.

## 2. Codec 0: PCM16

1600 samples, signed 16-bit little-endian, mono, 16 kHz. No compression, no state. Use it for
debugging and as the selectable alternative when bandwidth is not a concern (raw PCM16 at 16 kHz is
256 kbit/s).

## 3. Codec 1: IMA-ADPCM block

Each 100 ms chunk is one independent block (no state carried between chunks, so a lost frame costs
only its own 100 ms).

```
offset  size  field
0       2     predictor   s16 LE   = the chunk's first sample, exactly
2       1     step_index  u8       initial step index, 0..88 (the phone always writes 0)
3       1     reserved    u8       must be 0x00
4       800   nibbles             1599 4-bit codes for samples 2..1600, two per byte,
                                  LOW nibble first; the high nibble of byte 799 is padding and must be 0
```

Block length: 804 bytes. Sample `n` (1-based) for `n >= 2` uses code index `n - 2`: byte `(n-2) // 2`,
low nibble when `(n-2)` is even, high nibble when odd.

### 3.1 Tables

Step table: 89 entries (standard IMA/DVI). Index adjustment table: 16 entries, indexed by the full 4-bit
code (bit 3 is the sign, so the adjustment repeats for codes 8..15):

```
index_table = [-1, -1, -1, -1, 2, 4, 6, 8,  -1, -1, -1, -1, 2, 4, 6, 8]
```

Step table (`step_table[index]`):

```
 0:    7   1:    8   2:    9   3:   10   4:   11   5:   12   6:   13   7:   14   8:   16   9:   17
10:   19  11:   21  12:   23  13:   25  14:   28  15:   31  16:   34  17:   37  18:   41  19:   45
20:   50  21:   55  22:   60  23:   66  24:   73  25:   80  26:   88  27:   97  28:  107  29:  118
30:  130  31:  143  32:  157  33:  173  34:  190  35:  209  36:  230  37:  253  38:  279  39:  307
40:  337  41:  371  42:  408  43:  449  44:  494  45:  544  46:  598  47:  658  48:  724  49:  796
50:  876  51:  963  52: 1060  53: 1166  54: 1282  55: 1411  56: 1552  57: 1707  58: 1878  59: 2066
60: 2272  61: 2499  62: 2749  63: 3024  64: 3327  65: 3660  66: 4026  67: 4428  68: 4871  69: 5358
70: 5894  71: 6484  72: 7132  73: 7845  74: 8630  75: 9493  76:10442  77:11487  78:12635  79:13899
80:15289  81:16818  82:18500  83:20350  84:22385  85:24623  86:27086  87:29794  88:32767
```

### 3.2 Decoder (exact)

```
predictor = s16 from header; index = step_index from header
output[0] = predictor
for each code c (samples 2..1600 in order):
    step = step_table[index]
    diff = step >> 3
    if c & 4: diff += step
    if c & 2: diff += step >> 1
    if c & 1: diff += step >> 2
    predictor = predictor - diff if c & 8 else predictor + diff
    predictor = clamp(predictor, -32768, 32767)
    index = clamp(index + index_table[c], 0, 88)
    output[next] = predictor
```

The shift operations are on non-negative integers (`step` is positive), so there is no rounding
ambiguity. All arithmetic is exact integer arithmetic, so the three implementations must match bit for bit.

### 3.3 Encoder (exact)

The encoder runs the same state update as the decoder, so the two stay in lockstep.

```
predictor = samples[0]; index = 0          # header: predictor s16 LE, step_index 0, reserved 0
for each sample s in samples[1..1599]:
    step = step_table[index]
    diff = s - predictor
    c = 0
    if diff < 0: c = 8; diff = -diff
    if diff >= step:        c |= 4; diff -= step
    if diff >= step >> 1:   c |= 2; diff -= step >> 1
    if diff >= step >> 2:   c |= 1
    apply the decoder update (section 3.2) with code c   # updates predictor and index
    emit c
pack codes: byte = code[2k] | (code[2k+1] << 4); the last byte's high nibble is 0 (code 1599 is the pad)
```

Input validation: exactly 1600 samples, each an integer in -32768..32767. Anything else is rejected
(the encoder never truncates or pads silently).

### 3.4 Bad blocks: decoders drop them

A block is rejected (and the frame dropped, counted, and not played) when any of these holds:

- length is not exactly 804 bytes (short, odd or long);
- `step_index > 88`;
- the reserved byte is not `0x00`;
- the padding nibble (high nibble of byte 799) is not `0`.

A rejected block never produces partial output. Decoders never guess a predictor or index. Playback
simply has a 100 ms gap, and the next block starts fresh.

## 4. Server to viewer: `/audio`

`GET /audio` is a WebSocket that carries the phone's frames to a viewer (dashboard player or test client):

1. The first message is text, JSON, sent once when the socket is accepted:
   `{"type":"audio_hello","codec":1,"rate":16000,"chunk_ms":100,"station":"<id>"}`.
   `codec` is the codec of the frames that follow (the station's policy codec; `1` by default).
2. Every following message is binary and is the phone frame forwarded verbatim, **without the leading
   `0x02` byte**: `codec u8 | seq u32 LE | t_ms u32 LE | payload`. The server does not transcode.
3. Per-viewer buffering is bounded: a deque of 20 frames (2 s). When it is full the oldest frame is
   dropped. The viewer must tolerate gaps in `seq`.

Forwarding (the rule that a frame is relayed at all) is in the server design plan, not in this codec
document.

## 5. Decoder behaviour checklist (all languages)

- Parse the frame: check length >= 10, the codec is 0 or 1, and the payload length matches section 1.
  Codec 2 and other values are rejected.
- Codec 1: decode the block (section 3.2) and reject it under section 3.4.
- Keep the decoder state per block only. Do not reuse the previous chunk's predictor.
- Output 1600 samples per block at 16 kHz. Resampling for playback is the player's job.

## 6. Error bound

The codec is lossy. `tests/vectors/audio_v1.json` records, for each vector, the maximum absolute error
between the input and the reference decoder's output (`max_abs_error`) and the bound that the
generator enforces (`max_abs_error_bound`):

| vector | content | max_abs_error (reference) | bound |
|---|---|---|---|
| synthetic_mixed | 60 ms sine sweep 200 Hz to 4 kHz at amplitude 12000, 20 ms full-band noise burst, silence | 6313 | 8192 |
| synthetic_quiet | same shape at amplitude 300 | 149 | 512 |
| zeros | all zeros | 0 | 0 |
| square_full_scale | full-scale square wave, period 40 samples | 65524 | 65535 |

The square wave is a stress vector: 4-bit ADPCM cannot follow a jump of 65535 in a few samples, so it
checks clamping and exact agreement between implementations, not audio quality. Quality expectations for
speech-band audio are the `synthetic_quiet` and `synthetic_mixed` numbers.

## 7. Shared vectors

`tests/vectors/audio_v1.json` (generated by `python scripts/audio_vectors.py`, deterministic, fixed seed):

- `vectors[]`: `name`, `samples` (1600 ints), `block_hex` (804 bytes, the exact encoder output),
  `decoded` (1600 ints, the reference decoder's output), `max_abs_error`, `max_abs_error_bound`,
  `frame_hex` (the full codec 1 frame, seq 7, t_ms 1234) and `frame` (`seq`, `t_ms`).
- `reject_encode[]`: inputs the encoder must refuse (3 samples, 1599 samples).
- `reject_decode[]`: blocks the decoder must refuse (short, odd, long, step index 89, reserved byte set,
  padding nibble set).
- `step_table`, `index_table`: the tables above, repeated for implementers.

A copy lives at `android/app/src/test/resources/audio/audio_v1.json` so that the JVM tests load it from
the classpath. `tests/web/test_audio_codec.py` fails if the copy differs from the root file.
