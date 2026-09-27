import { describe, expect, it } from "vitest";
import { groupAudio, layoutSpans, leftPct, spanAt, ticks, timelineWindow } from "./timeline";
import type { Span } from "./types";

const S = (emotion: Span["emotion"], start: number, end: number): Span => ({ emotion, start, end, confidence: 0.8, reason: "r", source: "fused" });

describe("timeline", () => {
  it("window: session start to now, min 2 min, max 30 min", () => {
    expect(timelineWindow([], 1000)).toEqual({ start: 880, end: 1000 });
    expect(timelineWindow([S("happy", 500, 600)], 1000)).toEqual({ start: 500, end: 1000 });
    expect(timelineWindow([S("happy", 0, 10)], 5000)).toEqual({ start: 3200, end: 5000 });
  });
  it("leftPct clamps", () => {
    const w = { start: 0, end: 100 };
    expect(leftPct(25, w)).toBe(25);
    expect(leftPct(-5, w)).toBe(0);
    expect(leftPct(150, w)).toBe(100);
  });
  it("layoutSpans stretches the last span to now and drops spans outside the window", () => {
    const w = { start: 100, end: 200 };
    const out = layoutSpans([S("relaxed", 0, 50), S("happy", 50, 150), S("excited", 150, 160)], w, 200);
    expect(out.map((o) => [o.span.emotion, o.index, o.left, o.width])).toEqual([["happy", 1, 0, 50], ["excited", 2, 50, 50]]);
  });
  it("groupAudio merges repeats of the same label within the gap", () => {
    const w = { start: 0, end: 100 };
    const g = groupAudio([{ ts: 10, label: "yip", score: 1 }, { ts: 11.5, label: "yip", score: 1 }, { ts: 20, label: "bark", score: 1 }], w);
    expect(g.map((x) => [x.label, x.count, x.left])).toEqual([["yip", 2, 10], ["bark", 1, 20]]);
  });
  it("ticks: n+1 labels, last is now", () => {
    const t = ticks({ start: 0, end: 600 }, 6);
    expect(t).toHaveLength(7);
    expect(t[0].align).toBe("start");
    expect(t[6]).toMatchObject({ left: 100, label: "now", align: "end" });
    expect(t[1].label).toMatch(/^\d\d:\d\d$/);
    expect(ticks({ start: 0, end: 120 }, 6)[1].label).toMatch(/^\d\d:\d\d:\d\d$/); // < 1 min apart: add seconds so labels don't repeat
  });
  it("spanAt", () => {
    const spans = [S("relaxed", 0, 10), S("happy", 10, 20)];
    expect(spanAt(spans, 12)).toBe(1);
    expect(spanAt(spans, 99)).toBe(1); // after the end -> last span
    expect(spanAt([], 5)).toBe(-1);
  });
});
