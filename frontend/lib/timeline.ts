import { hhmm, hhmmss } from "./format";
import type { AudioMark, Span } from "./types";

export type TimeWindow = { start: number; end: number };

export function timelineWindow(spans: Span[], now: number, maxS = 1800, minS = 120): TimeWindow {
  const first = spans.length ? spans[0].start : now;
  let start = Math.max(first, now - maxS);
  if (now - start < minS) start = now - minS;
  return { start, end: now };
}

export function leftPct(t: number, w: TimeWindow): number {
  const span = w.end - w.start;
  if (span <= 0) return 0;
  return Math.min(100, Math.max(0, ((t - w.start) / span) * 100));
}

export function layoutSpans(spans: Span[], w: TimeWindow, now: number) {
  const out: { span: Span; index: number; left: number; width: number }[] = [];
  spans.forEach((span, index) => {
    const end = index === spans.length - 1 ? Math.max(span.end, now) : span.end;
    if (end <= w.start || span.start >= w.end) return;
    const left = leftPct(span.start, w);
    const width = leftPct(end, w) - left;
    if (width > 0) out.push({ span, index, left, width });
  });
  return out;
}

export function groupAudio(audio: AudioMark[], w: TimeWindow, gapS = 2) {
  const out: { ts: number; label: string; count: number; left: number }[] = [];
  let lastTs = -Infinity;
  for (const a of audio) {
    if (a.ts < w.start || a.ts > w.end) continue;
    const prev = out[out.length - 1];
    if (prev && prev.label === a.label && a.ts - lastTs <= gapS) {
      prev.count += 1;
    } else {
      out.push({ ts: a.ts, label: a.label, count: 1, left: leftPct(a.ts, w) });
    }
    lastTs = a.ts;
  }
  return out;
}

export function ticks(w: TimeWindow, n = 6) {
  const fmt = (w.end - w.start) / n < 60 ? hhmmss : hhmm; // the 2-min minimum window would repeat HH:MM labels
  return Array.from({ length: n + 1 }, (_, i) => {
    const t = w.start + ((w.end - w.start) * i) / n;
    return {
      left: (i / n) * 100,
      label: i === n ? "now" : fmt(t),
      align: (i === 0 ? "start" : i === n ? "end" : "center") as "start" | "center" | "end",
    };
  });
}

export function spanAt(spans: Span[], t: number): number {
  if (!spans.length) return -1;
  for (let i = spans.length - 1; i >= 0; i--) if (spans[i].start <= t) return i;
  return 0;
}
