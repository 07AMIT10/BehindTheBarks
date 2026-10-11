// Pure state for the dashboard "Listen to the room" control (Part A4). No DOM and no timers here:
// the availability selector, the connection reducer, the reconnect policy and the close-code texts.
import type { StationState } from "./phone";
import { StreamWatchdog } from "./watchdog";
import type { ViewMode } from "./types";

// ---- availability -------------------------------------------------------------------------------

export type ListenAvailability = "hidden" | "locked" | "ready";

export interface ListenInputs {
  /** effective_mode from the status: already min(station cap, household mode, viewer ceiling). */
  effectiveMode: ViewMode;
  /** The viewer's own mode_ceiling (full for PIN mode and owners). */
  viewerCeiling: ViewMode;
  stationState: StationState;
  tabVisible: boolean;
}

/**
 * Whether the Listen control is offered.
 * - hidden: the tab is hidden, the station is idle/paused/dropped, or the effective mode is not Full.
 * - locked: the viewer's ceiling is below Full (shown disabled with a lock and a tooltip).
 * - ready: Full mode, a live station and a viewer allowed to hear it.
 * Order matters: effectiveMode already includes the viewer ceiling, so a ceiling below Full is
 * checked before the mode, otherwise "locked" could never be shown.
 */
export function listenAvailability(i: ListenInputs): ListenAvailability {
  if (!i.tabVisible) return "hidden";
  if (i.stationState === "idle" || i.stationState === "paused" || i.stationState === "dropped") return "hidden";
  if (i.viewerCeiling !== "full") return "locked";
  if (i.effectiveMode !== "full") return "hidden";
  return "ready";
}

// ---- close codes and reconnect policy -----------------------------------------------------------

export const CLOSE_UNAUTHORIZED = 4401;
export const CLOSE_FORBIDDEN = 4403;
export const CLOSE_KICKED = 4411;
export const CLOSE_LOWERED = 4412;
export const MAX_RECONNECTS = 3;

/** Network-level failures are retried; every application close code (4xxx) and a normal 1000 close are not. */
export function shouldReconnect(closeCode: number, attempts: number): boolean {
  if (closeCode === 1000) return false;
  if (closeCode >= 4000 && closeCode <= 4999) return false;
  return attempts >= 0 && attempts < MAX_RECONNECTS;
}

/** Delay before retry number `attempt` (1-based): 500 ms, 1 s, 2 s. */
export function reconnectDelayMs(attempt: number): number {
  return 500 * 2 ** Math.max(0, attempt - 1);
}

// ---- reducer ------------------------------------------------------------------------------------

export type ListenErrorReason = "unauthorized" | "forbidden" | "kicked" | "lowered" | "network" | "ended" | "unsupported";

export type ListenState =
  | { kind: "idle" }
  | { kind: "connecting" }
  | { kind: "listening" }
  | { kind: "error"; reason: ListenErrorReason };

export type ListenEvent =
  | { type: "start" }
  | { type: "hello" }
  | { type: "retrying" }
  | { type: "closed"; code: number; willRetry: boolean }
  | { type: "stop" }
  | { type: "unsupported" };

/** The error reason a close code means, or null for a normal close (1000) that is not an error. */
export function closeReason(code: number): ListenErrorReason | null {
  switch (code) {
    case 1000:
      return null;
    case CLOSE_UNAUTHORIZED:
      return "unauthorized";
    case CLOSE_FORBIDDEN:
      return "forbidden";
    case CLOSE_KICKED:
      return "kicked";
    case CLOSE_LOWERED:
      return "lowered";
    default:
      return code >= 4000 && code <= 4999 ? "ended" : "network";
  }
}

export function listenReducer(state: ListenState, ev: ListenEvent): ListenState {
  switch (ev.type) {
    case "start":
      return state.kind === "connecting" || state.kind === "listening" ? state : { kind: "connecting" };
    case "hello":
      return state.kind === "connecting" || state.kind === "listening" ? { kind: "listening" } : state;
    case "retrying":
      return state.kind === "idle" ? state : { kind: "connecting" };
    case "closed": {
      const reason = closeReason(ev.code);
      if (reason === null) return { kind: "idle" };
      if (ev.willRetry) return { kind: "connecting" };
      return { kind: "error", reason };
    }
    case "stop":
      return { kind: "idle" };
    case "unsupported":
      return { kind: "error", reason: "unsupported" };
  }
}

/** Human text for the status line. */
export function listenErrorText(reason: ListenErrorReason): string {
  switch (reason) {
    case "unauthorized":
      return "Please sign in again";
    case "forbidden":
      return "Audio is only available in Full mode";
    case "kicked":
      return "A station user disconnected viewers";
    case "lowered":
      return "Sharing was lowered, audio stopped";
    case "network":
      return "Audio connection lost. Tap Listen to try again";
    case "ended":
      return "The audio stream ended";
    case "unsupported":
      return "This browser cannot play live audio";
  }
}

/** The status line for a state ("" when idle). */
export function listenStatusText(state: ListenState): string {
  switch (state.kind) {
    case "idle":
      return "";
    case "connecting":
      return "Connecting...";
    case "listening":
      return "Listening";
    case "error":
      return listenErrorText(state.reason);
  }
}

// ---- watchdog integration -----------------------------------------------------------------------

export interface ListenWatchdog {
  start(): void;
  stop(): void;
  /** Any user activity (mouse, key, touch, scroll) resets the idle timer. */
  activity(): void;
  /** Call on visibilitychange: a hidden tab stops listening at once. */
  visibility(): void;
}

/**
 * Reuses the video StreamWatchdog (180 s of inactivity or a hidden tab) and reports a pause as a stop.
 * The caller decides what stopping means; this only wires the timer and the visibility rule.
 */
export function createListenWatchdog(
  onIdle: () => void,
  maxIdleSec = 180,
  getVisibilityState?: () => DocumentVisibilityState,
): ListenWatchdog {
  const wd = new StreamWatchdog({ onPause: onIdle }, { maxIdleSec, getVisibilityState });
  return {
    start: () => wd.start(),
    stop: () => wd.stop(),
    activity: () => wd.recordActivity(),
    visibility: () => wd.handleVisibilityChange(),
  };
}
