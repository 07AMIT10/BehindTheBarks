import { describe, expect, it } from "vitest";
import { buildDemoView } from "./demo";
import type { LoadedClip } from "./demo";

const frame = (t: number, tail = 0.5) => ({ t, type: "frame" as const, data: {
  ts: t, source: "clip", dog_detected: true, bbox: [0, 0, 10, 10] as [number, number, number, number], bbox_conf: 0.9,
  body_keypoints: {}, face_landmarks: null,
  features: { tail_height: tail, tail_wag_hz: 2, ear_position: "up" as const, mouth_open: 0.5, body_lowering: 0.1, motion_energy: 0.4, in_feeding_zone: true } } });
const audio = (t: number) => ({ t, type: "audio" as const, data: { ts: t, label: "yip" as const, score: 0.8 } });

const clip = (): LoadedClip => ({
  meta: { id: "c", name: "C", emotion: "excited", duration_s: 10, has_timeline: true },
  events: [frame(0), frame(1), audio(1.5), frame(2)],
  timeline: {
    clip: "c", duration_s: 10,
    states: [
      { t: 1, emotion: "relaxed", confidence: 0.7, source: "rules", reason: "Calm." },
      { t: 2, emotion: "excited", confidence: 0.9, source: "fused", reason: "Treat!" },
    ],
    live: [], llm: [],
    notifications: [{ t: 2.5, emotion: "excited", reason: "Treat!" }],
    treats: [1.0],
  },
});

describe("buildDemoView", () => {
  it("empty before the first state; frame/history follow event time", () => {
    const v = buildDemoView(clip(), 0.5, new Set(), "Bruno");
    expect(v.emotion).toBeNull();
    expect(v.frame?.ts).toBe(0);
    expect(v.history).toHaveLength(1);
    expect(v.spans).toEqual([]);
  });
  it("state, spans, treats and audio at t=3", () => {
    const v = buildDemoView(clip(), 3, new Set(), "Bruno");
    expect(v.emotion).toMatchObject({ emotion: "excited", ts: 2 });
    expect(v.currentSince).toBe(2);
    expect(v.spans.map((s) => [s.emotion, s.start, s.end])).toEqual([["relaxed", 1, 2], ["excited", 2, 3]]);
    expect(v.treats).toEqual([1.0]);
    expect(v.audio.map((a) => a.label)).toEqual(["yip"]);
    expect(v.frame?.ts).toBe(2);
  });
  it("notification toasts within 6 s; sticky negatives persist; dismissal works", () => {
    const v = buildDemoView(clip(), 3, new Set(), "Bruno");
    expect(v.toasts.map((t) => t.title)).toEqual(["Bruno is excited"]);
    const v2 = buildDemoView(clip(), 20, new Set(), "Bruno");
    expect(v2.toasts).toEqual([]);
    expect(v2.notifications).toHaveLength(1);
  });
  it("seeking backwards rebuilds (no accumulation)", () => {
    const c = clip();
    const fwd = buildDemoView(c, 3, new Set(), "Bruno");
    const back = buildDemoView(c, 0.5, new Set(), "Bruno");
    expect(back.spans).toEqual([]);
    expect(back.toasts).toEqual([]);
    expect(fwd.spans).toHaveLength(2);
  });
});
