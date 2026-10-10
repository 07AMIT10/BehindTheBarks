// Dashboard-side reading of status.phone (the /camera page's connection, reported by the backend).
import type { Status } from "./types";

export type PhoneNotice = "blocked" | "dropped" | null;

/**
 * "blocked": a phone is connected but its camera permission was denied (streaming mic only).
 * "dropped": a phone streamed this session and its socket ended without the Stop button.
 * The backend forgets a phone that pressed Stop, so a deliberate stop shows nothing.
 */
export function phoneNotice(status: Status | null): PhoneNotice {
  const p = status?.phone;
  if (!p) return null;
  if (p.connected && p.camera === false) return "blocked";
  if (!p.connected) return "dropped";
  return null;
}

/** Shown for a deliberate Blackout: a neutral card, never a reconnecting or error state. */
export const PAUSED_NOTICE = "Sharing is paused — the station is in Blackout. Nothing leaves the phone.";

/**
 * The station as the dashboard should describe it. "idle" is the good, intentional state:
 * the phone pressed Stop (the backend forgets it and the pipeline reports `idle`), or no phone
 * has ever connected (first run). "paused" is a deliberate Blackout: the phone is alive and
 * silent on purpose, so it is neutral too. Only "dropped"/"stalled" mean something went wrong.
 */
export type StationState = "idle" | "paused" | "live" | "stalled" | "dropped" | "blocked";

export function stationState(s: Status | null): StationState {
  const p = s?.phone;
  const ps = s?.pipeline_status?.state;
  if (p?.connected && p.camera === false) return "blocked";
  if (p && !p.connected) return "dropped";
  if (ps === "idle") return "idle";
  if (ps === "paused") return "paused";
  if (ps === "stalled") return "stalled";
  return "live";
}

/** Status-bar device label. */
export function deviceLabel(s: Status | null): string {
  const p = s?.phone;
  if (p?.connected && s?.pipeline_status?.state === "paused") return `${p.device ?? "Phone"} · paused (Blackout)`;
  if (p?.connected) {
    const name = p.device ?? "Phone";
    return p.camera === false ? `${name} · microphone only` : `${name} · ${p.facing ?? "back"} camera`;
  }
  if (p && !p.connected) return `${p.device ?? "Phone"} · disconnected`;
  if (s?.pipeline_status?.state === "idle") return "No camera streaming";
  const src = s?.pipeline_status?.source;
  if (src === "mock") return "Mock camera";
  if (src === "browser") return "Phone camera · waiting";
  return src ? `${src} source` : "No camera yet";
}
