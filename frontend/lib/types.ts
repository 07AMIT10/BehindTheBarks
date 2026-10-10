import type { Toast } from "./toasts";
import type { AudioLabel, Emotion, EmotionState, Features, FrameEvent } from "./contracts";

/** T3.1: ascending exposure. A viewer sees min(station cap, household mode, their ceiling). */
export type ViewMode = "blackout" | "privacy" | "limited" | "full";


export type EnvelopeType = "frame" | "audio" | "rules" | "emotion" | "llm" | "notification" | "treat" | "status";

export type Envelope = {
  type: EnvelopeType;
  seq: number;
  ts: number; // server clock, seconds
  data: any; // eslint-disable-line @typescript-eslint/no-explicit-any -- narrowed per type in store.ts
  meta?: Record<string, unknown>;
  station?: string; // T4.1 / T4.2
};

export type PipelineStatus = {
  source: string;
  state: "running" | "idle" | "stalled" | "stopped" | string;
  fps: number;
  last_frame_age_s: number | null;
  audio_ok: boolean;
  preview_fps?: number;
  torch?: boolean;
  privacy_mode?: boolean;
  station_cap?: ViewMode;   // T3.1: what the phone allows
  effective_mode?: ViewMode; // T3.1: min(cap, household policy, viewer ceiling)
  policy_mode?: ViewMode;   // T3.1: what the household owner selected
};

export type PhoneStatus = {
  connected: boolean;
  device: string | null;
  facing: string | null; // "back" | "front" | null
  camera?: boolean; // false: camera permission denied, streaming microphone only
  fps: number; // frames/s received by the backend
  width?: number;
  height?: number;
  sample_rate?: number;
  last_frame_age_s?: number | null;
  battery_pct?: number | null; // T6.1
  charging?: boolean; // T6.1
  temp_c?: number | null; // T6.1
};

export type StationSummary = {
  id: string;
  name: string;
  dog_name: string;
  phone: PhoneStatus | null;
  pipeline_status: PipelineStatus | null;
  effective_mode: ViewMode;
  battery: number | null;
};

export type Status = {
  pipeline?: string;
  pipeline_status?: PipelineStatus | null;
  demo_mode?: boolean;
  llm?: {
    enabled: boolean;
    online: boolean;
    provider: string | null;
    model: string | null;
    vision: boolean | null;
    last_call: { latency_ms?: number; outcome?: string } | null;
  };
  notify_mode?: string;
  clients?: number;
  fps?: number;
  profile?: { dog_name: string; location: string; zone_label: string };
  phone?: PhoneStatus | null; // Plan 3: null until a phone has connected this session
  modes?: string[]; // Plan 4: ["live", "demo"] when demo clips exist
  mode?: "live" | "demo"; // Plan 4
  privacy_mode?: boolean;
  station_cap?: ViewMode;
  effective_mode?: ViewMode;
  mode_info?: { household_mode: ViewMode; station_cap: ViewMode; effective: ViewMode };
  max_idle_s?: number; // T3.4: video inactivity timeout in seconds
  viewers?: { video: number; audio: number }; // T3.4: viewer count breakdown
  stations?: StationSummary[]; // T4.1 / T4.2
};

export type Span = {
  emotion: Emotion;
  start: number;
  end: number;
  confidence: number;
  reason: string;
  source: "rules" | "llm" | "fused";
};

export type NotificationItem = {
  id: number;
  ts: number;
  state: EmotionState;
  status: "sent" | "failed" | "dashboard_only";
  detail: string;
  read: boolean;
};

export type AudioMark = { ts: number; label: AudioLabel; score: number };

export type StationView = {
  id: string;
  name: string;
  dog_name: string;
  status: Status | null;
  current: EmotionState | null;
  currentSince: number | null;
  lastEmotionAt: number | null;
  frame: FrameEvent | null;
  lastDogTs: number | null;
  history: Features[];
  lastHistoryTs: number | null;
  audio: AudioMark[];
  spans: Span[];
  treats: number[];
  notifications: NotificationItem[];
  lastSeq: number;
};

export type DashboardState = {
  connected: boolean;
  everConnected: boolean;
  reconnectAttempt: number;
  status: Status | null;
  current: EmotionState | null;
  currentSince: number | null; // server seconds when the current state began
  lastEmotionAt: number | null; // client ms when the last emotion message arrived
  frame: FrameEvent | null;
  lastDogTs: number | null;
  history: Features[];
  lastHistoryTs: number | null;
  audio: AudioMark[];
  spans: Span[];
  treats: number[];
  notifications: NotificationItem[];
  toasts: Toast[];
  lastSeq: number;
  clockOffset: number | null; // server seconds - client seconds
  stations: Record<string, StationView>; // T4.2
  focusedStation: string; // T4.2
};

export type Action =
  | { kind: "connected"; value: boolean }
  | { kind: "envelope"; env: Envelope; at: number }
  | { kind: "bootstrap"; events: Envelope[]; timeline: Span[]; at: number; stationId?: string }
  | { kind: "focusStation"; stationId: string }
  | { kind: "status"; status: Status }
  | { kind: "readAll" }
  | { kind: "tick"; at: number }
  | { kind: "dismissToast"; id: string };
