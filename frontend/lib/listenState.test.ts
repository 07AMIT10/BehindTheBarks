import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import {
  CLOSE_FORBIDDEN,
  CLOSE_KICKED,
  CLOSE_LOWERED,
  CLOSE_UNAUTHORIZED,
  type ListenInputs,
  type ListenState,
  closeReason,
  createListenWatchdog,
  listenAvailability,
  listenErrorText,
  listenReducer,
  listenStatusText,
  reconnectDelayMs,
  shouldReconnect,
} from "./listenState";
import type { StationState } from "./phone";
import type { ViewMode } from "./types";

const base: ListenInputs = { effectiveMode: "full", viewerCeiling: "full", stationState: "live", tabVisible: true };

describe("listenAvailability truth table", () => {
  const modes: ViewMode[] = ["blackout", "privacy", "limited", "full"];
  const stations: StationState[] = ["live", "stalled", "blocked", "idle", "paused", "dropped"];
  const offStation = new Set<StationState>(["idle", "paused", "dropped"]);

  for (const effectiveMode of modes) {
    for (const viewerCeiling of modes) {
      for (const stationState of stations) {
        for (const tabVisible of [true, false]) {
          const got = listenAvailability({ effectiveMode, viewerCeiling, stationState, tabVisible });
          const want = (() => {
            if (!tabVisible) return "hidden";
            if (offStation.has(stationState)) return "hidden";
            if (viewerCeiling !== "full") return "locked";
            if (effectiveMode !== "full") return "hidden";
            return "ready";
          })();
          it(`mode=${effectiveMode} ceiling=${viewerCeiling} station=${stationState} visible=${tabVisible} -> ${want}`, () => {
            expect(got).toBe(want);
          });
        }
      }
    }
  }

  it("is ready for a full, unrestricted viewer on a live station", () => {
    expect(listenAvailability(base)).toBe("ready");
    expect(listenAvailability({ ...base, stationState: "blocked" })).toBe("ready");
  });

  it("is locked for a limited viewer even though effective mode is limited too", () => {
    expect(listenAvailability({ ...base, effectiveMode: "limited", viewerCeiling: "limited" })).toBe("locked");
  });

  it("is hidden in Full mode when the household or station cap is lower", () => {
    expect(listenAvailability({ ...base, effectiveMode: "privacy" })).toBe("hidden");
  });
});

describe("shouldReconnect", () => {
  it("never retries the four refusal codes, whatever the attempt count", () => {
    for (const code of [4401, 4403, 4411, 4412]) {
      for (const attempts of [0, 1, 2, 3, 10]) {
        expect(shouldReconnect(code, attempts), `${code} attempt ${attempts}`).toBe(false);
      }
    }
  });

  it("never retries other application codes or a normal close", () => {
    for (const code of [1000, 4400, 4408, 4409, 4410]) {
      expect(shouldReconnect(code, 0), String(code)).toBe(false);
    }
  });

  it("retries network errors at most three times", () => {
    for (const code of [1006, 1001, 1011, 1005]) {
      expect(shouldReconnect(code, 0)).toBe(true);
      expect(shouldReconnect(code, 1)).toBe(true);
      expect(shouldReconnect(code, 2)).toBe(true);
      expect(shouldReconnect(code, 3)).toBe(false);
    }
  });

  it("backs off 500 ms, 1 s, 2 s", () => {
    expect([1, 2, 3].map(reconnectDelayMs)).toEqual([500, 1000, 2000]);
  });
});

describe("closeReason", () => {
  it("maps every close code the audio socket can return", () => {
    expect(closeReason(CLOSE_UNAUTHORIZED)).toBe("unauthorized");
    expect(closeReason(CLOSE_FORBIDDEN)).toBe("forbidden");
    expect(closeReason(CLOSE_KICKED)).toBe("kicked");
    expect(closeReason(CLOSE_LOWERED)).toBe("lowered");
    expect(closeReason(1000)).toBeNull();
    expect(closeReason(1006)).toBe("network");
    expect(closeReason(4400)).toBe("ended");
  });
});

describe("listenReducer", () => {
  const idle: ListenState = { kind: "idle" };
  const connecting: ListenState = { kind: "connecting" };
  const listening: ListenState = { kind: "listening" };

  it("start from idle connects; a second start does not reset a live connection", () => {
    expect(listenReducer(idle, { type: "start" })).toEqual(connecting);
    expect(listenReducer(listening, { type: "start" })).toEqual(listening);
  });

  it("hello moves connecting to listening; hello while idle is ignored", () => {
    expect(listenReducer(connecting, { type: "hello" })).toEqual(listening);
    expect(listenReducer(idle, { type: "hello" })).toEqual(idle);
  });

  it("retrying shows connecting unless stopped", () => {
    expect(listenReducer(listening, { type: "retrying" })).toEqual(connecting);
    expect(listenReducer(idle, { type: "retrying" })).toEqual(idle);
  });

  it("a close that will be retried goes back to connecting", () => {
    expect(listenReducer(listening, { type: "closed", code: 1006, willRetry: true })).toEqual(connecting);
  });

  it("every refusal close code becomes its own error", () => {
    const cases: [number, ListenState][] = [
      [4401, { kind: "error", reason: "unauthorized" }],
      [4403, { kind: "error", reason: "forbidden" }],
      [4411, { kind: "error", reason: "kicked" }],
      [4412, { kind: "error", reason: "lowered" }],
    ];
    for (const [code, want] of cases) {
      expect(listenReducer(listening, { type: "closed", code, willRetry: false }), String(code)).toEqual(want);
    }
  });

  it("a normal close is idle, a network failure without retries is an error", () => {
    expect(listenReducer(listening, { type: "closed", code: 1000, willRetry: false })).toEqual(idle);
    expect(listenReducer(connecting, { type: "closed", code: 1006, willRetry: false })).toEqual({
      kind: "error",
      reason: "network",
    });
  });

  it("stop always returns to idle, and unsupported is an error", () => {
    expect(listenReducer(listening, { type: "stop" })).toEqual(idle);
    expect(listenReducer(idle, { type: "unsupported" })).toEqual({ kind: "error", reason: "unsupported" });
  });
});

describe("listen texts", () => {
  it("gives the human text for each close code", () => {
    expect(listenErrorText("unauthorized")).toBe("Please sign in again");
    expect(listenErrorText("forbidden")).toBe("Audio is only available in Full mode");
    expect(listenErrorText("lowered")).toBe("Sharing was lowered, audio stopped");
    expect(listenErrorText("kicked")).toBe("A station user disconnected viewers");
  });

  it("gives the status line for each state", () => {
    expect(listenStatusText({ kind: "idle" })).toBe("");
    expect(listenStatusText({ kind: "connecting" })).toBe("Connecting...");
    expect(listenStatusText({ kind: "listening" })).toBe("Listening");
    expect(listenStatusText({ kind: "error", reason: "forbidden" })).toBe("Audio is only available in Full mode");
  });
});

describe("createListenWatchdog", () => {
  let visibility: DocumentVisibilityState;
  beforeEach(() => {
    vi.useFakeTimers();
    visibility = "visible";
  });
  afterEach(() => {
    vi.useRealTimers();
  });

  it("stops after 180 s without activity", () => {
    const onIdle = vi.fn();
    const wd = createListenWatchdog(onIdle, 180, () => visibility);
    wd.start();
    vi.advanceTimersByTime(179_000);
    expect(onIdle).not.toHaveBeenCalled();
    vi.advanceTimersByTime(1_000);
    expect(onIdle).toHaveBeenCalledTimes(1);
    wd.stop();
  });

  it("activity resets the 180 s timer", () => {
    const onIdle = vi.fn();
    const wd = createListenWatchdog(onIdle, 180, () => visibility);
    wd.start();
    vi.advanceTimersByTime(170_000);
    wd.activity();
    vi.advanceTimersByTime(170_000);
    expect(onIdle).not.toHaveBeenCalled();
    vi.advanceTimersByTime(10_000);
    expect(onIdle).toHaveBeenCalledTimes(1);
    wd.stop();
  });

  it("a hidden tab stops at once", () => {
    const onIdle = vi.fn();
    const wd = createListenWatchdog(onIdle, 180, () => visibility);
    wd.start();
    visibility = "hidden";
    wd.visibility();
    expect(onIdle).toHaveBeenCalledTimes(1);
    wd.stop();
  });

  it("stop cancels the timer", () => {
    const onIdle = vi.fn();
    const wd = createListenWatchdog(onIdle, 180, () => visibility);
    wd.start();
    wd.stop();
    vi.advanceTimersByTime(300_000);
    expect(onIdle).not.toHaveBeenCalled();
  });
});
