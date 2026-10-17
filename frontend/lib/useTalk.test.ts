import { describe, expect, it } from "vitest";

describe("two-way talk audio utilities", () => {
  it("correctly scales float32 audio samples to int16 PCM bounds", () => {
    const input = new Float32Array([-1.5, -1.0, -0.5, 0.0, 0.5, 1.0, 1.5]);
    const pcm = new Int16Array(input.length);
    for (let i = 0; i < input.length; i++) {
      const s = Math.max(-1, Math.min(1, input[i]));
      pcm[i] = s < 0 ? s * 0x8000 : s * 0x7fff;
    }
    expect(pcm[0]).toBe(-32768);
    expect(pcm[1]).toBe(-32768);
    expect(pcm[2]).toBe(-16384);
    expect(pcm[3]).toBe(0);
    expect(pcm[4]).toBe(16383);
    expect(pcm[5]).toBe(32767);
    expect(pcm[6]).toBe(32767);
  });

  it("encodes PCM16 buffer to valid Base64 string", () => {
    const pcm = new Int16Array([0, 1000, -1000, 32767, -32768]);
    const bytes = new Uint8Array(pcm.buffer, pcm.byteOffset, pcm.byteLength);
    let binary = "";
    for (let i = 0; i < bytes.byteLength; i++) {
      binary += String.fromCharCode(bytes[i]);
    }
    const b64 = btoa(binary);
    expect(typeof b64).toBe("string");
    expect(b64.length).toBeGreaterThan(0);

    // Decode and verify roundtrip
    const decodedBinary = atob(b64);
    const decodedBytes = new Uint8Array(decodedBinary.length);
    for (let i = 0; i < decodedBinary.length; i++) {
      decodedBytes[i] = decodedBinary.charCodeAt(i);
    }
    const decodedPcm = new Int16Array(decodedBytes.buffer);
    expect(Array.from(decodedPcm)).toEqual(Array.from(pcm));
  });

  it("handles linear interpolation downsampling correctly", () => {
    // 48kHz down to 16kHz (3:1 ratio)
    const ratio = 48000 / 16000;
    const input = new Float32Array([0.0, 0.3, 0.6, 0.9, 0.6, 0.3]);
    const outCount = Math.floor(input.length / ratio);
    const out = new Float32Array(outCount);
    for (let i = 0; i < outCount; i++) {
      const pos = i * ratio;
      const idx = Math.floor(pos);
      const frac = pos - idx;
      const s0 = input[idx] ?? 0;
      const s1 = input[idx + 1] ?? s0;
      out[i] = s0 + frac * (s1 - s0);
    }
    expect(outCount).toBe(2);
    expect(out[0]).toBeCloseTo(0.0);
    expect(out[1]).toBeCloseTo(0.9);
  });
});
