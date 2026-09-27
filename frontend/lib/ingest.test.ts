import { describe, expect, it } from "vitest";
import {
  audioMessage, backoffMs, buildHello, closeMessage, connectionQuality, countSince, deviceName, elapsedLabel,
  fitLongSide, floatToInt16, frameMessage, mediaErrorKind, meterBars, micLevel, parseFps, ratePerSecond, rms,
  shouldReconnect, shouldSkipFrame,
} from "./ingest";

describe("hello", () => {
  it("camera hello carries size, fps and optional fields", () => {
    const h = JSON.parse(buildHello({ width: 360.4, height: 640, fps: 8, sampleRate: 48000, device: "Pixel 7", facing: "back", camera: true }));
    expect(h).toEqual({ type: "hello", width: 360, height: 640, fps: 8, sample_rate: 48000, device: "Pixel 7", facing: "back", camera: true });
  });
  it("mic-only hello zeroes the video fields", () => {
    const h = JSON.parse(buildHello({ width: 640, height: 480, fps: 8, sampleRate: 44100, device: "iPhone", facing: "front", camera: false }));
    expect([h.width, h.height, h.fps, h.sample_rate, h.camera]).toEqual([0, 0, 0, 44100, false]);
  });
});

describe("framing", () => {
  it("frame message is 0x01 + jpeg", () => {
    expect(Array.from(frameMessage(new Uint8Array([0xff, 0xd8, 0x00])))).toEqual([1, 0xff, 0xd8, 0]);
  });
  it("float32 -> int16 clamps and scales", () => {
    expect(Array.from(floatToInt16(new Float32Array([0, 1, -1, 0.5, 2, -3])))).toEqual([0, 32767, -32768, 16384, 32767, -32768]);
  });
  it("audio message is 0x02 + int16 little-endian", () => {
    const m = audioMessage(new Float32Array([1, -1, 0]));
    expect(Array.from(m)).toEqual([2, 0xff, 0x7f, 0x00, 0x80, 0x00, 0x00]);
  });
});

describe("mic meter", () => {
  it("rms of a constant and of silence", () => {
    expect(rms(new Float32Array([0.5, -0.5, 0.5, -0.5]))).toBeCloseTo(0.5);
    expect(rms(new Float32Array(0))).toBe(0);
  });
  it("level is a dB scale from -60 to -10 dBFS", () => {
    expect(micLevel(0)).toBe(0);
    expect(micLevel(0.001)).toBe(0); // -60 dB
    expect(micLevel(0.01)).toBeCloseTo(0.4); // -40 dB
    expect(micLevel(0.5)).toBe(1);
  });
  it("bars light up in proportion", () => {
    const bars = meterBars(0.5, 14);
    expect(bars).toHaveLength(14);
    expect(bars.filter((b) => b.on)).toHaveLength(7);
    expect(bars[0]).toEqual({ h: 16, on: true });
    expect(bars[13]).toEqual({ h: 3, on: false });
    expect(meterBars(1, 10).every((b) => b.on && b.h >= 4)).toBe(true);
    expect(meterBars(0, 10).some((b) => b.on)).toBe(false);
  });
});

describe("frames", () => {
  it("downscales the long side to 640 and keeps portrait", () => {
    expect(fitLongSide(1920, 1080)).toEqual({ width: 640, height: 360 });
    expect(fitLongSide(720, 1280)).toEqual({ width: 360, height: 640 });
    expect(fitLongSide(320, 240)).toEqual({ width: 320, height: 240 });
    expect(fitLongSide(0, 0)).toEqual({ width: 0, height: 0 });
  });
  it("skips while encoding or when the socket is backed up", () => {
    expect(shouldSkipFrame(0, false)).toBe(false);
    expect(shouldSkipFrame(0, true)).toBe(true);
    expect(shouldSkipFrame(300_000, false)).toBe(true);
  });
});

describe("connection quality", () => {
  const ok = { connected: true, bufferedAmount: 0, reconnectsLastMinute: 0, skippedRatio: 0 };
  it("clean connection is 4 bars", () => {
    expect(connectionQuality(ok)).toEqual({ bars: 4, label: "Excellent" });
  });
  it("backlog, reconnects and skips each cost bars, never below 1 while connected", () => {
    expect(connectionQuality({ ...ok, bufferedAmount: 100_000 })).toEqual({ bars: 3, label: "Good" });
    expect(connectionQuality({ ...ok, bufferedAmount: 300_000 })).toEqual({ bars: 2, label: "Fair" });
    expect(connectionQuality({ ...ok, reconnectsLastMinute: 1, skippedRatio: 0.5 })).toEqual({ bars: 2, label: "Fair" });
    expect(connectionQuality({ ...ok, bufferedAmount: 2_000_000, reconnectsLastMinute: 2, skippedRatio: 0.9 })).toEqual({ bars: 1, label: "Poor" });
  });
  it("disconnected is offline", () => {
    expect(connectionQuality({ ...ok, connected: false })).toEqual({ bars: 0, label: "Offline" });
  });
});

describe("timing", () => {
  it("backoff doubles to a 5 s cap", () => {
    expect([1, 2, 3, 4, 5, 9].map(backoffMs)).toEqual([500, 1000, 2000, 4000, 5000, 5000]);
  });
  it("counts and rates over a window", () => {
    const t = [0, 125, 250, 375, 500, 625, 750, 875, 1000];
    expect(ratePerSecond(t, 1000)).toBe(8);
    expect(ratePerSecond(t, 10_000)).toBe(0);
    expect(countSince([0, 50_000, 59_000, 61_000], 60_000, 60_000)).toBe(2);
  });
  it("elapsed label", () => {
    expect(elapsedLabel(0)).toBe("0:00");
    expect(elapsedLabel(724_000)).toBe("12:04");
    expect(elapsedLabel(3_725_000)).toBe("1:02:05");
  });
});

describe("page params and environment", () => {
  it("fps param", () => {
    expect(parseFps("")).toBe(8);
    expect(parseFps("?fps=12")).toBe(12);
    expect(parseFps("?fps=99")).toBe(15);
    expect(parseFps("?fps=abc")).toBe(8);
    expect(parseFps("?backend=x&fps=0.4")).toBe(1);
    expect(parseFps("?fps=0")).toBe(8);
  });
  it("device name from user agent", () => {
    expect(deviceName("Mozilla/5.0 (iPhone; CPU iPhone OS 17_5 like Mac OS X) AppleWebKit/605.1.15")).toBe("iPhone");
    expect(deviceName("Mozilla/5.0 (Linux; Android 14; Pixel 7) AppleWebKit/537.36 Chrome/126 Mobile")).toBe("Pixel 7");
    expect(deviceName("Mozilla/5.0 (Linux; Android 10; K) AppleWebKit/537.36 Chrome/126 Mobile")).toBe("Android phone");
    expect(deviceName("Mozilla/5.0 (Linux; Android 9; SM-G960F Build/PPR1.180610.011) AppleWebKit")).toBe("SM-G960F");
    expect(deviceName("Mozilla/5.0 (X11; Linux x86_64)")).toBe("Browser");
  });
  it("media error kinds", () => {
    expect(mediaErrorKind("NotAllowedError", false)).toBe("insecure");
    expect(mediaErrorKind("NotAllowedError", true)).toBe("blocked");
    expect(mediaErrorKind("NotFoundError", true)).toBe("blocked");
    expect(mediaErrorKind("NotReadableError", true)).toBe("busy");
    expect(mediaErrorKind("TypeError", true)).toBe("other");
  });
  it("close codes", () => {
    expect(shouldReconnect(1006)).toBe(true);
    expect(shouldReconnect(4409)).toBe(true);
    expect(shouldReconnect(4400)).toBe(false);
    expect(shouldReconnect(4408)).toBe(false);
    expect(closeMessage(4409)).toMatch(/Another phone is already streaming/);
    expect(closeMessage(1006)).toBeNull();
  });
});
