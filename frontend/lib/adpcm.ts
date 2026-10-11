// IMA-ADPCM decoder and 0x02 frame parser for the dashboard audio player (docs/AUDIO_WIRE.md).
// Bit-exact with backend/web/audio_codec.py: every test vector must decode to the reference samples.
// Malformed input throws AdpcmError; the caller drops that frame or block and never plays a guess.

export const FRAME_MAGIC = 0x02;
export const FRAME_HEADER_BYTES = 10;
export const CODEC_PCM16 = 0;
export const CODEC_IMA_ADPCM = 1;
export const CHUNK_SAMPLES = 1600;
export const PCM16_PAYLOAD_BYTES = CHUNK_SAMPLES * 2;
export const ADPCM_BLOCK_BYTES = 804;
export const MAX_STEP_INDEX = 88;

export const STEP_TABLE: readonly number[] = [
  7, 8, 9, 10, 11, 12, 13, 14, 16, 17, 19, 21, 23, 25, 28, 31, 34, 37, 41, 45, 50, 55, 60, 66, 73,
  80, 88, 97, 107, 118, 130, 143, 157, 173, 190, 209, 230, 253, 279, 307, 337, 371, 408, 449, 494,
  544, 598, 658, 724, 796, 876, 963, 1060, 1166, 1282, 1411, 1552, 1707, 1878, 2066, 2272, 2499,
  2749, 3024, 3327, 3660, 4026, 4428, 4871, 5358, 5894, 6484, 7132, 7845, 8630, 9493, 10442, 11487,
  12635, 13899, 15289, 16818, 18500, 20350, 22385, 24623, 27086, 29794, 32767,
];

export const INDEX_TABLE: readonly number[] = [
  -1, -1, -1, -1, 2, 4, 6, 8, -1, -1, -1, -1, 2, 4, 6, 8,
];

export class AdpcmError extends Error {
  constructor(message: string) {
    super(message);
    this.name = "AdpcmError";
  }
}

export interface AudioFrame {
  codec: number;
  seq: number;
  tMs: number;
  payload: Uint8Array;
}

/** Parse and validate a phone frame (0x02 | codec u8 | seq u32 LE | t_ms u32 LE | payload). */
export function parseFrame(data: Uint8Array): AudioFrame {
  if (data.byteLength < FRAME_HEADER_BYTES) throw new AdpcmError("frame shorter than the 10-byte header");
  if (data[0] !== FRAME_MAGIC) throw new AdpcmError(`bad frame magic 0x${data[0].toString(16)}`);
  const codec = data[1];
  const view = new DataView(data.buffer, data.byteOffset, data.byteLength);
  const seq = view.getUint32(2, true);
  const tMs = view.getUint32(6, true);
  const payload = data.subarray(FRAME_HEADER_BYTES);
  const expected = payloadLength(codec);
  if (payload.byteLength !== expected) {
    throw new AdpcmError(`codec ${codec} payload must be ${expected} bytes, got ${payload.byteLength}`);
  }
  return { codec, seq, tMs, payload };
}

function payloadLength(codec: number): number {
  if (codec === CODEC_PCM16) return PCM16_PAYLOAD_BYTES;
  if (codec === CODEC_IMA_ADPCM) return ADPCM_BLOCK_BYTES;
  throw new AdpcmError(`codec ${codec} is not supported (0 PCM16, 1 IMA-ADPCM; 2 is reserved)`);
}

const clampPredictor = (v: number): number => (v < -32768 ? -32768 : v > 32767 ? 32767 : v);
const clampIndex = (v: number): number => (v < 0 ? 0 : v > MAX_STEP_INDEX ? MAX_STEP_INDEX : v);

/** Decode one 804-byte block to 1600 s16 samples. Throws AdpcmError on any malformed block. */
export function decodeBlock(block: Uint8Array): Int16Array {
  if (block.byteLength !== ADPCM_BLOCK_BYTES) {
    throw new AdpcmError(`ADPCM block must be ${ADPCM_BLOCK_BYTES} bytes, got ${block.byteLength}`);
  }
  const view = new DataView(block.buffer, block.byteOffset, block.byteLength);
  let predictor = view.getInt16(0, true);
  let index = block[2];
  if (index > MAX_STEP_INDEX) throw new AdpcmError(`step index ${index} out of range 0..${MAX_STEP_INDEX}`);
  if (block[3] !== 0) throw new AdpcmError("reserved header byte must be 0");
  if (block[ADPCM_BLOCK_BYTES - 1] >> 4 !== 0) throw new AdpcmError("padding nibble must be 0");

  const out = new Int16Array(CHUNK_SAMPLES);
  out[0] = predictor;
  for (let n = 0; n < CHUNK_SAMPLES - 1; n++) {
    const byte = block[4 + (n >> 1)];
    const code = n & 1 ? byte >> 4 : byte & 0x0f;
    const step = STEP_TABLE[index];
    let diff = step >> 3;
    if (code & 4) diff += step;
    if (code & 2) diff += step >> 1;
    if (code & 1) diff += step >> 2;
    predictor = clampPredictor(code & 8 ? predictor - diff : predictor + diff);
    index = clampIndex(index + INDEX_TABLE[code]);
    out[n + 1] = predictor;
  }
  return out;
}
