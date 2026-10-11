import { readFileSync } from "node:fs";
import { join } from "node:path";
import { describe, expect, it } from "vitest";
import {
  ADPCM_BLOCK_BYTES,
  AdpcmError,
  CODEC_IMA_ADPCM,
  INDEX_TABLE,
  STEP_TABLE,
  decodeBlock,
  parseFrame,
} from "./adpcm";

// The same vectors file the Python and Kotlin codecs assert (tests/vectors/audio_v1.json).
interface Vector {
  name: string;
  block_hex: string;
  decoded: number[];
  max_abs_error: number;
  max_abs_error_bound: number;
  samples: number[];
  frame_hex: string;
  frame: { seq: number; t_ms: number };
}
interface VectorFile {
  step_table: number[];
  index_table: number[];
  vectors: Vector[];
  reject_decode: { name: string; block_hex: string }[];
}

const doc: VectorFile = JSON.parse(
  readFileSync(join(import.meta.dirname, "../../tests/vectors/audio_v1.json"), "utf8"),
);

function hex(s: string): Uint8Array {
  return Uint8Array.from(s.match(/../g) ?? [], (b) => parseInt(b, 16));
}

describe("IMA-ADPCM tables (docs/AUDIO_WIRE.md)", () => {
  it("match the shared vectors file", () => {
    expect(Array.from(STEP_TABLE)).toEqual(doc.step_table);
    expect(Array.from(INDEX_TABLE)).toEqual(doc.index_table);
    expect(STEP_TABLE).toHaveLength(89);
  });
});

describe("decodeBlock against the shared vectors", () => {
  for (const name of ["synthetic_mixed", "synthetic_quiet", "zeros", "square_full_scale"]) {
    const v = doc.vectors.find((x) => x.name === name);
    it(`reproduces ${name} exactly`, () => {
      if (!v) throw new Error(`vector ${name} missing`);
      const block = hex(v.block_hex);
      expect(block.byteLength).toBe(ADPCM_BLOCK_BYTES);
      expect(Array.from(decodeBlock(block))).toEqual(v.decoded);
    });
    it(`keeps ${name} within its recorded error bound`, () => {
      if (!v) throw new Error(`vector ${name} missing`);
      const decoded = decodeBlock(hex(v.block_hex));
      let err = 0;
      v.samples.forEach((s, i) => {
        err = Math.max(err, Math.abs(s - decoded[i]));
      });
      expect(err).toBe(v.max_abs_error);
      expect(err).toBeLessThanOrEqual(v.max_abs_error_bound);
    });
  }

  it("rejects every malformed block in the vectors file", () => {
    for (const c of doc.reject_decode) {
      expect(() => decodeBlock(hex(c.block_hex)), c.name).toThrow(AdpcmError);
    }
  });
});

describe("parseFrame", () => {
  it("parses every vector frame and returns the block payload", () => {
    for (const v of doc.vectors) {
      const frame = parseFrame(hex(v.frame_hex));
      expect(frame.codec).toBe(CODEC_IMA_ADPCM);
      expect(frame.seq).toBe(v.frame.seq);
      expect(frame.tMs).toBe(v.frame.t_ms);
      expect(Array.from(frame.payload)).toEqual(Array.from(hex(v.block_hex)));
    }
  });

  it("rejects a wrong magic, reserved and unknown codecs, and wrong payload lengths", () => {
    const good = hex(doc.vectors[0].frame_hex);
    const withMagic = Uint8Array.from(good);
    withMagic[0] = 0x03;
    expect(() => parseFrame(withMagic)).toThrow(AdpcmError);
    const codec2 = Uint8Array.from(good);
    codec2[1] = 2;
    expect(() => parseFrame(codec2)).toThrow(AdpcmError);
    const codec9 = Uint8Array.from(good);
    codec9[1] = 9;
    expect(() => parseFrame(codec9)).toThrow(AdpcmError);
    expect(() => parseFrame(good.subarray(0, good.byteLength - 1))).toThrow(AdpcmError);
    expect(() => parseFrame(new Uint8Array([2, 1, 0, 0]))).toThrow(AdpcmError);
  });
});
