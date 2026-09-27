// Pure helpers for the /camera page and the /ingest protocol (CLAUDE.md, "Phone camera").
// No DOM access here: everything is unit-tested in ingest.test.ts.

export const KIND_FRAME = 0x01;
export const KIND_AUDIO = 0x02;

export const CLOSE_BAD_HELLO = 4400;
export const CLOSE_REPLACED = 4408;
export const CLOSE_BUSY = 4409;

export const DEFAULT_FPS = 8;
export const MAX_FPS = 15;
export const FRAME_LONG_SIDE = 640;
export const JPEG_QUALITY = 0.7;
export const FRAME_SKIP_BYTES = 256 * 1024; // skip a frame while this much is still queued in the socket
export const METER_BARS_PORTRAIT = 14;
export const METER_BARS_LANDSCAPE = 10;

export type Facing = "back" | "front";

export type HelloInput = {
  width: number;
  height: number;
  fps: number;
  sampleRate: number;
  device: string;
  facing: Facing;
  camera: boolean;
};

/** The first /ingest message. A mic-only phone (camera false) reports 0 × 0 at 0 fps. */
export function buildHello(h: HelloInput): string {
  return JSON.stringify({
    type: "hello",
    width: h.camera ? Math.round(h.width) : 0,
    height: h.camera ? Math.round(h.height) : 0,
    fps: h.camera ? h.fps : 0,
    sample_rate: Math.round(h.sampleRate),
    device: h.device,
    facing: h.facing,
    camera: h.camera,
  });
}

/** 0x01 + JPEG bytes. */
export function frameMessage(jpeg: Uint8Array): Uint8Array {
  const out = new Uint8Array(jpeg.length + 1);
  out[0] = KIND_FRAME;
  out.set(jpeg, 1);
  return out;
}

/** Float32 [-1, 1] -> Int16 (clamped; -1 -> -32768, 1 -> 32767). */
export function floatToInt16(samples: Float32Array): Int16Array {
  const out = new Int16Array(samples.length);
  for (let i = 0; i < samples.length; i++) {
    const s = Math.max(-1, Math.min(1, samples[i]));
    out[i] = s < 0 ? Math.round(s * 0x8000) : Math.round(s * 0x7fff);
  }
  return out;
}

/** 0x02 + mono Int16 little-endian PCM, converted from Float32 samples. */
export function audioMessage(samples: Float32Array): Uint8Array {
  const pcm = floatToInt16(samples);
  const out = new Uint8Array(1 + pcm.length * 2);
  out[0] = KIND_AUDIO;
  const view = new DataView(out.buffer);
  for (let i = 0; i < pcm.length; i++) view.setInt16(1 + i * 2, pcm[i], true);
  return out;
}

export function rms(samples: Float32Array): number {
  if (samples.length === 0) return 0;
  let sum = 0;
  for (let i = 0; i < samples.length; i++) sum += samples[i] * samples[i];
  return Math.sqrt(sum / samples.length);
}

/** RMS -> 0..1 on a dB scale: -60 dBFS (or silence) is 0, -10 dBFS and louder is 1. */
export function micLevel(r: number): number {
  if (r <= 0) return 0;
  const db = 20 * Math.log10(r);
  return Math.min(1, Math.max(0, (db + 60) / 50));
}

export type MeterBar = { h: number; on: boolean };

/** Meter bars for a 0..1 level: lit bars step down from 16 px, unlit bars are 3 px dots. */
export function meterBars(level: number, count: number): MeterBar[] {
  const lit = Math.round(Math.min(1, Math.max(0, level)) * count);
  return Array.from({ length: count }, (_, i) =>
    i < lit ? { h: Math.max(4, Math.round(16 * (1 - (0.5 * i) / count))), on: true } : { h: 3, on: false },
  );
}

/** Scale (w, h) so the long side is at most maxLong, keeping the aspect ratio (portrait stays portrait). */
export function fitLongSide(w: number, h: number, maxLong = FRAME_LONG_SIDE): { width: number; height: number } {
  if (w <= 0 || h <= 0) return { width: 0, height: 0 };
  const s = Math.min(1, maxLong / Math.max(w, h));
  return { width: Math.max(1, Math.round(w * s)), height: Math.max(1, Math.round(h * s)) };
}

export function shouldSkipFrame(bufferedAmount: number, encoding: boolean, threshold = FRAME_SKIP_BYTES): boolean {
  return encoding || bufferedAmount > threshold;
}

export type Quality = { bars: 0 | 1 | 2 | 3 | 4; label: "Excellent" | "Good" | "Fair" | "Poor" | "Offline" };

const QUALITY_LABELS = ["Offline", "Poor", "Fair", "Good", "Excellent"] as const;

/**
 * Connection quality from what the page can observe cheaply:
 * start at 4 bars; -1 if > 64 KB is queued in the socket, -2 if > 256 KB, -3 if > 1 MB;
 * -1 if the socket reconnected in the last minute; -1 if more than 20% of frames were skipped.
 * Connected is never below 1 bar; not connected is 0 ("Offline").
 */
export function connectionQuality(q: {
  connected: boolean;
  bufferedAmount: number;
  reconnectsLastMinute: number;
  skippedRatio: number;
}): Quality {
  if (!q.connected) return { bars: 0, label: "Offline" };
  let bars = 4;
  if (q.bufferedAmount > 1024 * 1024) bars -= 3;
  else if (q.bufferedAmount > 256 * 1024) bars -= 2;
  else if (q.bufferedAmount > 64 * 1024) bars -= 1;
  if (q.reconnectsLastMinute > 0) bars -= 1;
  if (q.skippedRatio > 0.2) bars -= 1;
  const b = Math.max(1, bars) as Quality["bars"];
  return { bars: b, label: QUALITY_LABELS[b] };
}

/** Reconnect delay, same curve as the dashboard: 0.5 s, 1 s, 2 s, 4 s, then 5 s. */
export function backoffMs(attempt: number): number {
  return Math.min(5000, 500 * 2 ** Math.max(0, attempt - 1));
}

/** Events in the last windowMs. */
export function countSince(timesMs: number[], nowMs: number, windowMs: number): number {
  let n = 0;
  for (const t of timesMs) if (t > nowMs - windowMs && t <= nowMs) n++;
  return n;
}

/** Whole events per second over the last windowMs: (n - 1) / span of the events inside it. */
export function ratePerSecond(timesMs: number[], nowMs: number, windowMs = 3000): number {
  const recent = timesMs.filter((t) => t > nowMs - windowMs && t <= nowMs);
  if (recent.length < 2) return 0;
  const span = (recent[recent.length - 1] - recent[0]) / 1000;
  return span > 0 ? Math.round((recent.length - 1) / span) : 0;
}

/** Stream clock: "m:ss" under an hour, then "h:mm:ss". */
export function elapsedLabel(ms: number): string {
  const s = Math.max(0, Math.floor(ms / 1000));
  const pad = (n: number) => String(n).padStart(2, "0");
  const h = Math.floor(s / 3600);
  const m = Math.floor((s % 3600) / 60);
  return h > 0 ? `${h}:${pad(m)}:${pad(s % 60)}` : `${m}:${pad(s % 60)}`;
}

/** ?fps= from the page URL, clamped to 1..15; DEFAULT_FPS when missing or invalid. */
export function parseFps(search: string): number {
  const raw = new URLSearchParams(search).get("fps");
  const n = raw === null ? NaN : Number(raw);
  if (!Number.isFinite(n) || n <= 0) return DEFAULT_FPS;
  return Math.min(MAX_FPS, Math.max(1, Math.round(n)));
}

/** A short device label for the dashboard status bar, from the user agent. */
export function deviceName(ua: string): string {
  if (/iPhone/.test(ua)) return "iPhone";
  if (/iPad/.test(ua)) return "iPad";
  const android = ua.match(/Android [\d.]+; ([^;)]+)[;)]/);
  if (android) {
    const model = android[1].replace(/\s+Build\/.*$/, "").trim();
    return model && model !== "K" ? model : "Android phone";
  }
  if (/Android/.test(ua)) return "Android phone";
  if (/Macintosh/.test(ua)) return "Mac";
  if (/Windows/.test(ua)) return "Windows PC";
  return "Browser";
}

export type MediaErrorKind = "insecure" | "blocked" | "busy" | "other";

/** Classify a getUserMedia failure by DOMException name. */
export function mediaErrorKind(name: string | undefined, secureContext: boolean): MediaErrorKind {
  if (!secureContext) return "insecure";
  if (name === "NotAllowedError" || name === "SecurityError" || name === "NotFoundError" || name === "OverconstrainedError")
    return "blocked";
  if (name === "NotReadableError" || name === "AbortError") return "busy";
  return "other";
}

/** Whether the page should reconnect after the backend closed the socket with this code. */
export function shouldReconnect(code: number): boolean {
  return code !== CLOSE_BAD_HELLO && code !== CLOSE_REPLACED;
}

/** What the streaming view shows after a close; null for ordinary drops (the pill says "Reconnecting…"). */
export function closeMessage(code: number): string | null {
  if (code === CLOSE_BUSY) return "Another phone is already streaming. This one takes over when it stops.";
  if (code === CLOSE_REPLACED) return "Another phone took over the stream.";
  if (code === CLOSE_BAD_HELLO) return "The backend rejected this page. Reload it and try again.";
  return null;
}

export type ZoneGuide = { left: string; right: string; top: string; bottom: string };

// Dashed feeding-zone guide, in % of the preview box. Setup: ui/screens/05 (exact). Streaming: the
// pixel rects of 06 (390x844: 10/10/278/330) and 07 (844x390: 120/124/36/32) converted to %.
export const ZONE_SETUP: ZoneGuide = { left: "22%", right: "12%", top: "30%", bottom: "14%" };
export const ZONE_PORTRAIT: ZoneGuide = { left: "2.6%", right: "2.6%", top: "32.9%", bottom: "39.1%" };
export const ZONE_LANDSCAPE: ZoneGuide = { left: "14.2%", right: "14.7%", top: "9.2%", bottom: "8.2%" };
