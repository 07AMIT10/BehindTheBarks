import { describe, expect, it } from "vitest";
import { initialState, reduce, serverNow } from "./store";
import type { Envelope, Span } from "./types";

let seq = 0;
const env = (type: Envelope["type"], data: unknown, ts: number, meta?: Record<string, unknown>): Envelope =>
  ({ type, seq: ++seq, ts, data, ...(meta ? { meta } : {}) }) as Envelope;
const emotion = (e: string, ts: number, changed: boolean, conf = 0.8) =>
  env("emotion", { ts, emotion: e, confidence: conf, source: "fused", reason: `${e} reason`, snapshot: null }, ts, { changed });
const frame = (ts: number, dog = true, tail = 0.5) =>
  env("frame", {
    ts, source: "mock", dog_detected: dog, bbox: dog ? [0, 0, 10, 10] : null, bbox_conf: 0.9, body_keypoints: {},
    face_landmarks: null,
    features: { tail_height: tail, tail_wag_hz: 2, ear_position: "up", mouth_open: 0.5, body_lowering: 0.1, motion_energy: 0.4, in_feeding_zone: true },
  }, ts);
const apply = (actions: Parameters<typeof reduce>[1][]) => actions.reduce(reduce, initialState);

describe("reduce", () => {
  it("connection flags", () => {
    const s = apply([{ kind: "connected", value: true }, { kind: "connected", value: false }]);
    expect(s.connected).toBe(false);
    expect(s.everConnected).toBe(true);
    expect(s.reconnectAttempt).toBe(1);
  });

  it("first emotion sets current and since; changed opens a new span", () => {
    const s = apply([
      { kind: "envelope", env: emotion("relaxed", 100, true), at: 1_000 },
      { kind: "envelope", env: emotion("relaxed", 101, false), at: 2_000 },
      { kind: "envelope", env: emotion("excited", 104, true), at: 5_000 },
    ]);
    expect(s.current?.emotion).toBe("excited");
    expect(s.currentSince).toBe(104);
    expect(s.lastEmotionAt).toBe(5_000);
    expect(s.spans.map((x: Span) => [x.emotion, x.start, x.end])).toEqual([["relaxed", 100, 104], ["excited", 104, 104]]);
  });

  it("unchanged emotion extends the last span", () => {
    const s = apply([
      { kind: "envelope", env: emotion("happy", 10, true), at: 0 },
      { kind: "envelope", env: emotion("happy", 15, false, 0.6), at: 0 },
    ]);
    expect(s.spans).toHaveLength(1);
    expect(s.spans[0].end).toBe(15);
    expect(s.spans[0].confidence).toBe(0.6);
  });

  it("frames update latest frame, dog-last-seen and a sampled feature history", () => {
    const actions = [0, 0.1, 0.2, 0.6, 1.2].map((t) => ({ kind: "envelope" as const, env: frame(50 + t), at: 0 }));
    const s = apply([...actions, { kind: "envelope", env: frame(52, false), at: 0 }]);
    expect(s.frame?.dog_detected).toBe(false);
    expect(s.lastDogTs).toBeCloseTo(51.2);
    expect(s.history).toHaveLength(3); // samples >= 0.5 s apart: 50, 50.6, 51.2
  });

  it("history is capped at 24 samples", () => {
    const actions = Array.from({ length: 40 }, (_, i) => ({ kind: "envelope" as const, env: frame(i), at: 0 }));
    expect(apply(actions).history).toHaveLength(24);
  });

  it("audio keeps dog sounds only; treats and notifications recorded", () => {
    const s = apply([
      { kind: "envelope", env: env("audio", { ts: 1, label: "silence", score: 0.9 }, 1), at: 0 },
      { kind: "envelope", env: env("audio", { ts: 2, label: "yip", score: 0.8 }, 2), at: 0 },
      { kind: "envelope", env: env("treat", { ts: 3 }, 3), at: 0 },
      { kind: "envelope", env: env("notification", { state: { ts: 4, emotion: "anxious", confidence: 0.7, source: "rules", reason: "r", snapshot: null }, status: "dashboard_only", channel: "dashboard", detail: "would send to owner" }, 4), at: 0 },
    ]);
    expect(s.audio.map((a) => a.label)).toEqual(["yip"]);
    expect(s.treats).toEqual([3]);
    expect(s.notifications[0]).toMatchObject({ status: "dashboard_only", read: false });
    expect(s.notifications[0].state.emotion).toBe("anxious");
    expect(reduce(s, { kind: "readAll" }).notifications[0].read).toBe(true);
  });

  it("status envelope replaces or merges status", () => {
    const full = { pipeline: "mock", fps: 8, profile: { dog_name: "Bruno", location: "Kitchen", zone_label: "feeding area" } };
    let s = apply([{ kind: "envelope", env: env("status", full, 1), at: 0 }]);
    s = reduce(s, { kind: "envelope", env: env("status", { phone: { connected: true, device: "Pixel 7", facing: "back", fps: 12 } }, 2), at: 0 });
    expect(s.status?.pipeline).toBe("mock");
    expect(s.status?.phone?.device).toBe("Pixel 7");
  });

  it("bootstrap uses server timeline for spans and replays the rest", () => {
    const timeline: Span[] = [{ emotion: "relaxed", start: 1, end: 9, confidence: 0.7, reason: "r", source: "rules" }];
    const s = reduce(initialState, {
      kind: "bootstrap", timeline, at: 20_000,
      events: [emotion("relaxed", 9, false), env("treat", { ts: 5 }, 5)],
    });
    expect(s.spans).toEqual(timeline);
    expect(s.current?.emotion).toBe("relaxed");
    expect(s.currentSince).toBe(1);
    expect(s.treats).toEqual([5]);
    expect(s.lastSeq).toBeGreaterThan(0);
  });

  it("server clock offset", () => {
    const s = apply([{ kind: "envelope", env: emotion("happy", 1000, true), at: 990_000 }]);
    expect(serverNow(s, 995_000)).toBeCloseTo(1005);
  });
});

describe("toasts in the reducer", () => {
  const notif = (e: string, ts: number) =>
    env("notification", { state: { ts, emotion: e, confidence: 0.8, source: "fused", reason: `${e}!`, snapshot: null }, status: "dashboard_only", channel: "dashboard", detail: "would send to owner" }, ts);
  const withProfile = reduce(initialState, { kind: "status", status: { pipeline: "mock", profile: { dog_name: "Bruno", location: "Kitchen", zone_label: "feeding area" } } });

  it("notification creates a toast using the dog name", () => {
    const s = reduce(withProfile, { kind: "envelope", env: notif("anxious", 10), at: 1_000 });
    expect(s.toasts[0]).toMatchObject({ title: "Bruno seems anxious", sticky: true });
  });
  it("tick expires positive toasts after 6 s; dismiss removes", () => {
    let s = reduce(withProfile, { kind: "envelope", env: notif("excited", 10), at: 1_000 });
    s = reduce(s, { kind: "tick", at: 7_500 });
    expect(s.toasts).toHaveLength(0);
    s = reduce(s, { kind: "envelope", env: notif("fearful", 11), at: 8_000 });
    s = reduce(s, { kind: "dismissToast", id: s.toasts[0].id });
    expect(s.toasts).toHaveLength(0);
  });
  it("AI going offline and back produces system toasts", () => {
    const llm = (online: boolean) => ({ enabled: true, online, provider: "p", model: "m", vision: true, last_call: null });
    let s = reduce(withProfile, { kind: "status", status: { pipeline: "mock", llm: llm(true) } });
    expect(s.toasts).toHaveLength(0);
    s = reduce(s, { kind: "status", status: { pipeline: "mock", llm: llm(false) } });
    expect(s.toasts[0]).toMatchObject({ kind: "system", title: "AI offline · rules only" });
    s = reduce(s, { kind: "status", status: { pipeline: "mock", llm: llm(true) } });
    expect(s.toasts[0].title).toBe("AI back online · fused readings resumed");
  });
  it("bootstrap does not toast old notifications", () => {
    const s = reduce(withProfile, { kind: "bootstrap", events: [notif("anxious", 5)], timeline: [], at: 1 });
    expect(s.notifications).toHaveLength(1);
    expect(s.toasts).toHaveLength(0);
  });
});
