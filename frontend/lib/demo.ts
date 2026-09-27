import type { AudioLabel, Emotion, EmotionState, Features, FrameEvent } from "./contracts";
import { NEGATIVE } from "./emotions";
import { FADE_MS, type Toast } from "./toasts";
import type { AudioMark, NotificationItem, Span } from "./types";

export type TimedState = { t: number; emotion: Emotion; confidence: number; source: "rules" | "llm" | "fused"; reason: string };
export type ClipMeta = { id: string; name: string; emotion: Emotion; duration_s: number; has_timeline: boolean };
export type ClipEvent = { t: number; type: "frame" | "audio" | "rules" | "treat"; data: any }; // eslint-disable-line @typescript-eslint/no-explicit-any
export type DemoTimeline = {
  clip: string; duration_s: number; states: TimedState[]; live: TimedState[]; llm: unknown[];
  notifications: { t: number; emotion: Emotion; reason: string }[]; treats: number[];
};
export type LoadedClip = { meta: ClipMeta; events: ClipEvent[]; timeline: DemoTimeline };

export type DemoView = {
  frame: FrameEvent | null; history: Features[]; emotion: EmotionState | null; currentSince: number | null;
  spans: Span[]; audio: AudioMark[]; treats: number[]; notifications: NotificationItem[]; toasts: Toast[];
};

export async function fetchClip(http: string, meta: ClipMeta): Promise<LoadedClip> {
  const get = async (path: string) => {
    const r = await fetch(`${http}${path}`, { cache: "no-store" });
    if (!r.ok) throw new Error(`${path}: HTTP ${r.status}`);
    return r.json();
  };
  const [raw, timeline] = await Promise.all([
    get(`/demo/clips/${meta.id}/events`).then((d: { events: ClipEvent[] }) => d.events),
    get(`/demo/clips/${meta.id}/timeline`),
  ]);
  // Person A lines carry no t; the time lives in data.ts (clip-relative via --rebase-ts).
  const events = raw
    .map((e) => ({ ...e, t: typeof e.t === "number" ? e.t : (e.data as { ts?: unknown }).ts }))
    .filter((e): e is ClipEvent => typeof e.t === "number")
    .sort((a, b) => a.t - b.t);
  return { meta, events, timeline: timeline as DemoTimeline };
}

const asState = (s: TimedState): EmotionState => ({ ts: s.t, emotion: s.emotion, confidence: s.confidence, source: s.source, reason: s.reason, snapshot: null });

export function buildDemoView(clip: LoadedClip, t: number, dismissed: ReadonlySet<number>, dogName: string): DemoView {
  const frames = clip.events.filter((e) => e.type === "frame" && e.data.ts <= t);
  const frame = (frames[frames.length - 1]?.data as FrameEvent | undefined) ?? null;
  const history = frames.slice(-24).map((e) => (e.data as FrameEvent).features);
  const past = clip.timeline.states.filter((s) => s.t <= t);
  const cur = past[past.length - 1] ?? null;
  const spans: Span[] = past.map((s, i) => ({
    emotion: s.emotion, start: s.t,
    end: i + 1 < past.length ? past[i + 1].t : t,
    confidence: s.confidence, reason: s.reason, source: s.source,
  }));
  const audio: AudioMark[] = clip.events
    .filter((e) => e.type === "audio" && e.data.ts <= t)
    .map((e) => ({ ts: e.data.ts as number, label: e.data.label as AudioLabel, score: e.data.score as number }));
  const treats = clip.timeline.treats.filter((x) => x <= t);
  const notifications: NotificationItem[] = clip.timeline.notifications
    .map((n, i) => ({ id: i, ts: n.t, state: { ...asState({ ...n, confidence: 0.8, source: "rules" as const }), ts: n.t }, status: "dashboard_only" as const, detail: "Demo playback", read: true }))
    .filter((n) => n.ts <= t);
  // Demo toasts are rebuilt deterministically from clip time (seek-safe): sticky negatives
  // persist until dismissed (by notification index); positives show for 6 s of play time.
  const toasts: Toast[] = [];
  for (const n of notifications) {
    if (dismissed.has(n.id)) continue;
    const negative = NEGATIVE.has(n.state.emotion);
    if (!negative && (t - n.ts) * 1000 > FADE_MS) continue;
    toasts.push({ id: `demo-${n.id}`, kind: negative ? "negative" : "positive", emotion: n.state.emotion,
      title: `${dogName} ${negative ? "seems" : "is"} ${n.state.emotion}`, body: n.state.reason, ts: n.ts,
      status: "dashboard_only", detail: "Demo playback", count: 1, sticky: negative, createdAt: n.ts * 1000 });
  }
  return { frame, history, emotion: cur ? asState(cur) : null, currentSince: cur ? cur.t : null, spans, audio, treats, notifications, toasts };
}
