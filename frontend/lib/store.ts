import type { AudioEvent, EmotionState, FrameEvent } from "./contracts";
import { dismissToast, expireToasts, systemToast, toastFromNotification } from "./toasts";
import type { Action, DashboardState, Envelope, NotificationItem, Span, Status } from "./types";

export const HISTORY_MAX = 24;
export const HISTORY_EVERY_S = 0.5;
const AUDIO_MAX = 400;
const NOTIFICATIONS_MAX = 50;
const DOG_SOUNDS = new Set(["bark", "yip", "growl", "whimper", "howl"]);

export const initialState: DashboardState = {
  connected: false,
  everConnected: false,
  reconnectAttempt: 0,
  status: null,
  current: null,
  currentSince: null,
  lastEmotionAt: null,
  frame: null,
  lastDogTs: null,
  history: [],
  lastHistoryTs: null,
  audio: [],
  spans: [],
  treats: [],
  notifications: [],
  toasts: [],
  lastSeq: 0,
  clockOffset: null,
};

export function serverNow(s: DashboardState, clientMs: number): number {
  return clientMs / 1000 + (s.clockOffset ?? 0);
}

function applyEmotion(s: DashboardState, e: EmotionState, changed: boolean, at: number, touchSpans: boolean): DashboardState {
  const first = s.current === null;
  const isNew = first || changed || s.current?.emotion !== e.emotion;
  let spans = s.spans;
  if (touchSpans) {
    const last = spans[spans.length - 1];
    if (!last || (isNew && last.emotion !== e.emotion) || changed) {
      const closed = last ? [...spans.slice(0, -1), { ...last, end: e.ts }] : spans;
      spans = [...closed, { emotion: e.emotion, start: e.ts, end: e.ts, confidence: e.confidence, reason: e.reason, source: e.source }];
    } else {
      spans = [...spans.slice(0, -1), { ...last, end: e.ts, confidence: e.confidence, reason: e.reason, source: e.source }];
    }
  }
  return {
    ...s,
    current: e,
    currentSince: isNew ? e.ts : s.currentSince,
    lastEmotionAt: at,
    spans,
  };
}

function applyFrame(s: DashboardState, f: FrameEvent): DashboardState {
  let { history, lastHistoryTs } = s;
  if (f.dog_detected && (lastHistoryTs === null || f.ts - lastHistoryTs >= HISTORY_EVERY_S - 1e-9)) {
    history = [...history, f.features].slice(-HISTORY_MAX);
    lastHistoryTs = f.ts;
  }
  return { ...s, frame: f, lastDogTs: f.dog_detected ? f.ts : s.lastDogTs, history, lastHistoryTs };
}

function applyEnvelope(s: DashboardState, env: Envelope, at: number, touchSpans: boolean, live: boolean): DashboardState {
  const base: DashboardState = {
    ...s,
    lastSeq: Math.max(s.lastSeq, env.seq ?? 0),
    clockOffset: typeof env.ts === "number" && at > 0 ? env.ts - at / 1000 : s.clockOffset,
  };
  switch (env.type) {
    case "emotion":
      return applyEmotion(base, env.data as EmotionState, Boolean(env.meta?.changed), at, touchSpans);
    case "frame":
      return applyFrame(base, env.data as FrameEvent);
    case "audio": {
      const a = env.data as AudioEvent;
      if (!DOG_SOUNDS.has(a.label)) return base;
      return { ...base, audio: [...base.audio, { ts: a.ts, label: a.label, score: a.score }].slice(-AUDIO_MAX) };
    }
    case "treat":
      return { ...base, treats: [...base.treats, (env.data as { ts: number }).ts] };
    case "notification": {
      const d = env.data as { state: EmotionState; status: NotificationItem["status"]; detail: string };
      const item: NotificationItem = { id: env.seq, ts: d.state.ts, state: d.state, status: d.status, detail: d.detail ?? "", read: false };
      const notifications = [...base.notifications, item].slice(-NOTIFICATIONS_MAX);
      const dog = base.status?.profile?.dog_name ?? "Your dog";
      return { ...base, notifications, toasts: live ? toastFromNotification(base.toasts, item, dog, at) : base.toasts };
    }
    case "status": {
      const d = env.data as Status;
      const isFull = "pipeline" in d && "profile" in d;
      return withStatus(base, isFull ? d : { ...(base.status ?? {}), ...d }, at);
    }
    default:
      return base; // rules, llm: not stored (status poll carries LLM latency)
  }
}

function withStatus(s: DashboardState, next: Status, at: number): DashboardState {
  const was = s.status?.llm;
  const now = next.llm;
  let toasts = s.toasts;
  if (was?.enabled && now?.enabled && was.online !== now.online) {
    toasts = systemToast(toasts, now.online ? "AI back online · fused readings resumed" : "AI offline · rules only", at);
  }
  return { ...s, status: next, toasts };
}

export function reduce(s: DashboardState, a: Action): DashboardState {
  switch (a.kind) {
    case "connected":
      return a.value
        ? { ...s, connected: true, everConnected: true, reconnectAttempt: 0 }
        : { ...s, connected: false, reconnectAttempt: s.everConnected || s.connected ? s.reconnectAttempt + 1 : s.reconnectAttempt };
    case "envelope":
      return applyEnvelope(s, a.env, a.at, true, true);
    case "bootstrap": {
      const reset: DashboardState = { ...s, spans: [], treats: [], audio: [], notifications: [], current: null, currentSince: null };
      let next = a.events.reduce((acc, e) => applyEnvelope(acc, e, a.at, false, false), reset);
      next = { ...next, spans: a.timeline };
      const last: Span | undefined = a.timeline[a.timeline.length - 1];
      if (last && next.current && last.emotion === next.current.emotion) next = { ...next, currentSince: last.start };
      return next;
    }
    case "status":
      return withStatus(s, a.status, Date.now());
    case "readAll":
      return { ...s, notifications: s.notifications.map((n) => ({ ...n, read: true })) };
    case "tick":
      return { ...s, toasts: expireToasts(s.toasts, a.at) };
    case "dismissToast":
      return { ...s, toasts: dismissToast(s.toasts, a.id) };
  }
}
