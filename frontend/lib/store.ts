import type { AudioEvent, EmotionState, FrameEvent } from "./contracts";
import { dismissToast, expireToasts, systemToast, toastFromNotification } from "./toasts";
import { shouldToast } from "./battery";
import type { Action, DashboardState, Envelope, NotificationItem, Span, StationView, Status } from "./types";

export const HISTORY_MAX = 24;
export const HISTORY_EVERY_S = 0.5;
const AUDIO_MAX = 400;
const NOTIFICATIONS_MAX = 50;
const DOG_SOUNDS = new Set(["bark", "yip", "growl", "whimper", "howl"]);

export function initialStationView(id: string = "default", name: string = "Default", dogName: string = "Your dog"): StationView {
  return {
    id,
    name,
    dog_name: dogName,
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
    lastSeq: 0,
  };
}

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
  stations: { default: initialStationView("default") },
  focusedStation: "default",
};

export function serverNow(s: DashboardState, clientMs: number): number {
  return clientMs / 1000 + (s.clockOffset ?? 0);
}

function applyStationEmotion(sv: StationView, e: EmotionState, changed: boolean, at: number, touchSpans: boolean): StationView {
  const first = sv.current === null;
  const isNew = first || changed || sv.current?.emotion !== e.emotion;
  let spans = sv.spans;
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
    ...sv,
    current: e,
    currentSince: isNew ? e.ts : sv.currentSince,
    lastEmotionAt: at,
    spans,
  };
}

function applyStationFrame(sv: StationView, f: FrameEvent): StationView {
  let { history, lastHistoryTs } = sv;
  if (f.dog_detected && (lastHistoryTs === null || f.ts - lastHistoryTs >= HISTORY_EVERY_S - 1e-9)) {
    history = [...history, f.features].slice(-HISTORY_MAX);
    lastHistoryTs = f.ts;
  }
  return { ...sv, frame: f, lastDogTs: f.dog_detected ? f.ts : sv.lastDogTs, history, lastHistoryTs };
}

function applyStationEnvelope(sv: StationView, env: Envelope, at: number, touchSpans: boolean): StationView {
  const base: StationView = {
    ...sv,
    lastSeq: Math.max(sv.lastSeq, env.seq ?? 0),
  };
  switch (env.type) {
    case "emotion":
      return applyStationEmotion(base, env.data as EmotionState, Boolean(env.meta?.changed), at, touchSpans);
    case "frame":
      return applyStationFrame(base, env.data as FrameEvent);
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
      return { ...base, notifications: [...base.notifications, item].slice(-NOTIFICATIONS_MAX) };
    }
    case "status": {
      const d = env.data as Status;
      const isFull = "pipeline" in d && "profile" in d;
      return { ...base, status: isFull ? d : { ...(base.status ?? {}), ...d } };
    }
    default:
      return base;
  }
}

export function syncFocused(s: DashboardState): DashboardState {
  const f = s.stations[s.focusedStation] ?? s.stations["default"] ?? Object.values(s.stations)[0];
  if (!f) return s;
  return {
    ...s,
    current: f.current,
    currentSince: f.currentSince,
    lastEmotionAt: f.lastEmotionAt,
    frame: f.frame,
    lastDogTs: f.lastDogTs,
    history: f.history,
    lastHistoryTs: f.lastHistoryTs,
    audio: f.audio,
    spans: f.spans,
    treats: f.treats,
    notifications: f.notifications,
    lastSeq: f.lastSeq,
    status: f.status ?? s.status,
  };
}

function withStatus(s: DashboardState, next: Status, at: number): DashboardState {
  const was = s.status?.llm;
  const now = next.llm;
  let toasts = s.toasts;
  if (was?.enabled && now?.enabled && was.online !== now.online) {
    toasts = systemToast(toasts, now.online ? "AI back online · fused readings resumed" : "AI offline · rules only", at);
  }

  // T6.2: Low battery alert toasts
  const oldStations = s.status?.stations ?? [];
  const newStations = next.stations ?? [];
  if (newStations.length > 0) {
    for (const newSt of newStations) {
      const oldSt = oldStations.find((o) => o.id === newSt.id);
      const prevPct = oldSt?.phone?.battery_pct ?? oldSt?.battery ?? null;
      const nextPct = newSt.phone?.battery_pct ?? newSt.battery ?? null;
      const charging = Boolean(newSt.phone?.charging);
      const alert = shouldToast(prevPct, nextPct, charging);
      if (alert) {
        const label = newSt.name || "Station";
        const msg = alert === "critical"
          ? `${label} battery critical (${Math.round(nextPct!)}%)`
          : `${label} battery low (${Math.round(nextPct!)}%)`;
        toasts = systemToast(toasts, msg, at);
      }
    }
  } else if (next.phone) {
    const prevPct = s.status?.phone?.battery_pct ?? null;
    const nextPct = next.phone.battery_pct ?? null;
    const charging = Boolean(next.phone.charging);
    const alert = shouldToast(prevPct, nextPct, charging);
    if (alert) {
      const msg = alert === "critical"
        ? `Station battery critical (${Math.round(nextPct!)}%)`
        : `Station battery low (${Math.round(nextPct!)}%)`;
      toasts = systemToast(toasts, msg, at);
    }
  }

  return { ...s, status: next, toasts };
}

function applyEnvelope(s: DashboardState, env: Envelope, at: number, touchSpans: boolean, live: boolean): DashboardState {
  const stationId = env.station ?? "default";
  const prevSv = s.stations[stationId] ?? initialStationView(stationId);
  const nextSv = applyStationEnvelope(prevSv, env, at, touchSpans);
  const nextStations = { ...s.stations, [stationId]: nextSv };

  let toasts = s.toasts;
  if (env.type === "notification" && live) {
    const d = env.data as { state: EmotionState; status: NotificationItem["status"]; detail: string };
    const item: NotificationItem = { id: env.seq, ts: d.state.ts, state: d.state, status: d.status, detail: d.detail ?? "", read: false };
    const dog = s.status?.profile?.dog_name ?? "Your dog";
    const title = nextSv.name && nextSv.name !== "Default" ? `${dog} in ${nextSv.name}` : dog;
    toasts = toastFromNotification(s.toasts, item, title, at);
  } else if (env.type === "status") {
    const d = env.data as Status;
    const isFull = "pipeline" in d && "profile" in d;
    return syncFocused(withStatus({
      ...s,
      stations: nextStations,
      lastSeq: Math.max(s.lastSeq, env.seq ?? 0),
      clockOffset: typeof env.ts === "number" && at > 0 ? env.ts - at / 1000 : s.clockOffset,
    }, isFull ? d : { ...(s.status ?? {}), ...d }, at));
  }

  return syncFocused({
    ...s,
    stations: nextStations,
    toasts,
    lastSeq: Math.max(s.lastSeq, env.seq ?? 0),
    clockOffset: typeof env.ts === "number" && at > 0 ? env.ts - at / 1000 : s.clockOffset,
  });
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
      const stationId = a.stationId ?? s.focusedStation ?? "default";
      const prevSv = s.stations[stationId] ?? initialStationView(stationId);
      const resetSv: StationView = { ...prevSv, spans: [], treats: [], audio: [], notifications: [], current: null, currentSince: null };
      let nextSv = a.events.reduce((acc, e) => applyStationEnvelope(acc, e, a.at, false), resetSv);
      nextSv = { ...nextSv, spans: a.timeline };
      const last: Span | undefined = a.timeline[a.timeline.length - 1];
      if (last && nextSv.current && last.emotion === nextSv.current.emotion) nextSv = { ...nextSv, currentSince: last.start };
      const nextStations = { ...s.stations, [stationId]: nextSv };
      return syncFocused({
        ...s,
        stations: nextStations,
        lastSeq: Math.max(s.lastSeq, ...a.events.map((e) => e.seq ?? 0)),
      });
    }
    case "focusStation":
      return syncFocused({ ...s, focusedStation: a.stationId });
    case "status": {
      const nextStations = { ...s.stations };
      if (a.status.stations && a.status.stations.length > 0) {
        for (const st of a.status.stations) {
          const existing = nextStations[st.id] ?? initialStationView(st.id, st.name, st.dog_name);
          nextStations[st.id] = {
            ...existing,
            name: st.name || existing.name,
            dog_name: st.dog_name || existing.dog_name,
          };
        }
        if (s.focusedStation === "default" && !a.status.stations.some((st) => st.id === "default")) {
          return syncFocused(withStatus({ ...s, stations: nextStations, focusedStation: a.status.stations[0].id }, a.status, Date.now()));
        }
      }
      return syncFocused(withStatus({ ...s, stations: nextStations }, a.status, Date.now()));
    }
    case "readAll": {
      const nextStations = Object.fromEntries(
        Object.entries(s.stations).map(([id, sv]) => [
          id,
          { ...sv, notifications: sv.notifications.map((n) => ({ ...n, read: true })) },
        ])
      );
      return syncFocused({
        ...s,
        stations: nextStations,
        notifications: s.notifications.map((n) => ({ ...n, read: true })),
      });
    }
    case "tick":
      return { ...s, toasts: expireToasts(s.toasts, a.at) };
    case "dismissToast":
      return { ...s, toasts: dismissToast(s.toasts, a.id) };
  }
}
