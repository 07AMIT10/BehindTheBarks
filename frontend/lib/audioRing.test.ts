import { describe, expect, it } from "vitest";
import { AudioRing, levelPercent, rms } from "./audioRing";

const chunk = (n: number, value: number) => new Float32Array(n).fill(value);
const ramp = (from: number, n: number) => Float32Array.from({ length: n }, (_, i) => from + i);

describe("AudioRing sizes", () => {
  it("defaults to 300 ms target and 1 s maximum at 16 kHz", () => {
    const r = new AudioRing();
    expect(r.targetSamples).toBe(4800);
    expect(r.capacity).toBe(16000);
    expect(r.priming).toBe(true);
  });
});

describe("AudioRing target (priming)", () => {
  it("stays silent and uncounted until the target is buffered", () => {
    const r = new AudioRing();
    r.push(chunk(1600, 0.5));
    r.push(chunk(1600, 0.5));
    expect(r.priming).toBe(true);
    const out = new Float32Array(128).fill(9);
    expect(r.read(out)).toBe(0);
    expect(Array.from(out).every((v) => v === 0)).toBe(true);
    expect(r.underruns).toBe(0);
    expect(r.buffered).toBe(3200); // reads while priming do not consume
    r.push(chunk(1600, 0.5));
    expect(r.priming).toBe(false);
  });

  it("plays the buffered samples in order once primed", () => {
    const r = new AudioRing({ targetMs: 10, maxMs: 1000, sampleRate: 1000 }); // target 10 samples
    r.push(ramp(0, 10));
    const out = new Float32Array(4);
    expect(r.read(out)).toBe(0);
    expect(Array.from(out)).toEqual([0, 1, 2, 3]);
    expect(r.buffered).toBe(6);
  });
});

describe("AudioRing underrun", () => {
  it("fills silence, counts the missing samples and re-primes", () => {
    const r = new AudioRing({ targetMs: 10, maxMs: 1000, sampleRate: 1000 });
    r.push(ramp(1, 10));
    const out = new Float32Array(12).fill(7);
    const missing = r.read(out);
    expect(missing).toBe(2);
    expect(Array.from(out.subarray(0, 10))).toEqual([1, 2, 3, 4, 5, 6, 7, 8, 9, 10]);
    expect(Array.from(out.subarray(10))).toEqual([0, 0]);
    expect(r.underruns).toBe(1);
    expect(r.silentSamples).toBe(2);
    expect(r.priming).toBe(true);
    // Next read is silence without another count until the ring refills to target.
    const next = new Float32Array(4).fill(3);
    expect(r.read(next)).toBe(0);
    expect(r.underruns).toBe(1);
  });

  it("counts one underrun per short read, not per missing sample", () => {
    const r = new AudioRing({ targetMs: 2, maxMs: 1000, sampleRate: 1000 });
    r.push(chunk(2, 0.1));
    r.read(new Float32Array(5));
    expect(r.underruns).toBe(1);
    expect(r.silentSamples).toBe(3);
  });
});

describe("AudioRing overrun", () => {
  it("drops the oldest samples when a push exceeds the 1 s maximum", () => {
    const r = new AudioRing({ targetMs: 2, maxMs: 10, sampleRate: 1000 }); // capacity 10
    r.push(ramp(0, 8));
    const dropped = r.push(ramp(100, 5)); // 13 buffered -> drop 3 oldest
    expect(dropped).toBe(3);
    expect(r.overrunSamples).toBe(3);
    expect(r.buffered).toBe(10);
    const out = new Float32Array(10);
    r.read(out);
    expect(Array.from(out)).toEqual([3, 4, 5, 6, 7, 100, 101, 102, 103, 104]);
  });

  it("keeps only the newest capacity samples when one push is larger than the ring", () => {
    const r = new AudioRing({ targetMs: 2, maxMs: 4, sampleRate: 1000 });
    const dropped = r.push(ramp(0, 9));
    expect(dropped).toBe(5);
    const out = new Float32Array(4);
    r.read(out);
    expect(Array.from(out)).toEqual([5, 6, 7, 8]);
  });

  it("keeps order across the wrap-around point", () => {
    const r = new AudioRing({ targetMs: 2, maxMs: 6, sampleRate: 1000 });
    r.push(ramp(0, 6));
    const first = new Float32Array(4);
    r.read(first); // head moves to 4
    r.push(ramp(10, 4)); // wraps
    const out = new Float32Array(6);
    r.read(out);
    expect(Array.from(first)).toEqual([0, 1, 2, 3]);
    expect(Array.from(out)).toEqual([4, 5, 10, 11, 12, 13]);
  });
});

describe("AudioRing clear", () => {
  it("empties the ring and primes again", () => {
    const r = new AudioRing();
    r.push(chunk(4800, 0.2));
    expect(r.priming).toBe(false);
    r.clear();
    expect(r.buffered).toBe(0);
    expect(r.priming).toBe(true);
  });
});

describe("rms and levelPercent", () => {
  it("rms of a constant block is its magnitude, empty is 0", () => {
    expect(rms(chunk(100, -0.5))).toBeCloseTo(0.5, 6);
    expect(rms(new Float32Array(0))).toBe(0);
  });

  it("maps silence to 0, full scale to 100, and uses a dB scale", () => {
    expect(levelPercent(0)).toBe(0);
    expect(levelPercent(1)).toBe(100);
    expect(levelPercent(0.001)).toBe(0); // exactly -60 dB
    expect(levelPercent(10 ** (-30 / 20))).toBe(50); // -30 dB
    expect(levelPercent(0.00001)).toBe(0); // quieter than -60 dB clamps to 0
  });
});
