import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { StreamWatchdog } from "./watchdog";

describe("StreamWatchdog (T3.4 Anti-lurking Inactivity Watchdog)", () => {
  beforeEach(() => {
    vi.useFakeTimers();
  });

  afterEach(() => {
    vi.useRealTimers();
  });

  it("triggers pause after default 180 seconds of inactivity", () => {
    let paused = false;
    const watchdog = new StreamWatchdog({
      onPause: () => {
        paused = true;
      },
    });

    watchdog.start();
    expect(paused).toBe(false);

    // Fast-forward 179 seconds
    vi.advanceTimersByTime(179_000);
    expect(paused).toBe(false);

    // Hit 180 seconds
    vi.advanceTimersByTime(1_000);
    expect(paused).toBe(true);
    expect(watchdog.paused).toBe(true);
  });

  it("resets timeout when user interaction is recorded", () => {
    let paused = false;
    const watchdog = new StreamWatchdog({
      onPause: () => {
        paused = true;
      },
    });

    watchdog.start();

    // Advance 100s, then record mouse/keyboard movement
    vi.advanceTimersByTime(100_000);
    watchdog.recordActivity();

    // Advance another 100s (total 200s, but only 100s since activity)
    vi.advanceTimersByTime(100_000);
    expect(paused).toBe(false);

    // Advance another 80s (reaches 180s from last activity)
    vi.advanceTimersByTime(80_000);
    expect(paused).toBe(true);
  });

  it("pauses immediately when page/tab visibility becomes hidden", () => {
    let paused = false;
    let visibility: DocumentVisibilityState = "visible";

    const watchdog = new StreamWatchdog(
      {
        onPause: () => {
          paused = true;
        },
      },
      {
        getVisibilityState: () => visibility,
      }
    );

    watchdog.start();
    expect(paused).toBe(false);

    // Tab switched to background
    visibility = "hidden";
    watchdog.handleVisibilityChange();

    expect(paused).toBe(true);
    expect(watchdog.paused).toBe(true);
  });

  it("supports configurable maxIdleSec", () => {
    let paused = false;
    const watchdog = new StreamWatchdog(
      {
        onPause: () => {
          paused = true;
        },
      },
      {
        maxIdleSec: 30,
      }
    );

    watchdog.start();
    vi.advanceTimersByTime(29_000);
    expect(paused).toBe(false);

    vi.advanceTimersByTime(1_000);
    expect(paused).toBe(true);
  });

  it("resumes only upon explicit resume() and notifies onResume", () => {
    let paused = false;
    let resumed = false;

    const watchdog = new StreamWatchdog({
      onPause: () => {
        paused = true;
      },
      onResume: () => {
        resumed = true;
      },
    });

    watchdog.start();
    vi.advanceTimersByTime(180_000);
    expect(paused).toBe(true);

    // Activity while paused does not resume
    watchdog.recordActivity();
    expect(watchdog.paused).toBe(true);
    expect(resumed).toBe(false);

    // Explicit resume
    watchdog.resume();
    expect(watchdog.paused).toBe(false);
    expect(resumed).toBe(true);

    // Timer restarts after resume
    vi.advanceTimersByTime(179_000);
    expect(watchdog.paused).toBe(false);
    vi.advanceTimersByTime(1_000);
    expect(watchdog.paused).toBe(true);
  });

  it("cleans up timer on stop()", () => {
    let paused = false;
    const watchdog = new StreamWatchdog({
      onPause: () => {
        paused = true;
      },
    });

    watchdog.start();
    watchdog.stop();

    vi.advanceTimersByTime(200_000);
    expect(paused).toBe(false);
  });
});
