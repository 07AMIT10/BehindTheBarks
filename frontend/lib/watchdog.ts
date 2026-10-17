/**
 * T3.4 Inactivity Watchdog: pauses live video streaming after continuous inactivity
 * (default 180s) or immediately when the tab is hidden (document.visibilityState === "hidden").
 * Resuming requires explicit user action ("Tap to resume"), creating a fresh stream.
 */

export type WatchdogListener = {
  onPause: () => void;
  onResume?: () => void;
};

export type WatchdogConfig = {
  maxIdleSec?: number;
  getVisibilityState?: () => DocumentVisibilityState;
};

export class StreamWatchdog {
  private timeoutId: ReturnType<typeof setTimeout> | null = null;
  private maxIdleMs: number;
  private isPaused: boolean = false;
  private onPause: () => void;
  private onResume?: () => void;
  private getVisibility: () => DocumentVisibilityState;

  constructor(listener: WatchdogListener, config: WatchdogConfig = {}) {
    this.onPause = listener.onPause;
    this.onResume = listener.onResume;
    this.maxIdleMs = Math.max(1, (config.maxIdleSec ?? 180)) * 1000;
    this.getVisibility =
      config.getVisibilityState ??
      (() => (typeof document !== "undefined" ? document.visibilityState : "visible"));
  }

  start(): void {
    this.isPaused = false;
    this.resetTimer();
  }

  stop(): void {
    this.clearTimer();
  }

  recordActivity(): void {
    if (this.isPaused) {
      return;
    }
    this.resetTimer();
  }

  handleVisibilityChange(): void {
    if (this.getVisibility() === "hidden") {
      this.pause();
    }
  }

  resume(): void {
    this.isPaused = false;
    this.onResume?.();
    this.resetTimer();
  }

  pause(): void {
    this.clearTimer();
    if (!this.isPaused) {
      this.isPaused = true;
      this.onPause();
    }
  }

  get paused(): boolean {
    return this.isPaused;
  }

  private resetTimer(): void {
    this.clearTimer();
    if (this.getVisibility() === "hidden") {
      this.pause();
      return;
    }
    this.timeoutId = setTimeout(() => {
      this.pause();
    }, this.maxIdleMs);
  }

  private clearTimer(): void {
    if (this.timeoutId !== null) {
      clearTimeout(this.timeoutId);
      this.timeoutId = null;
    }
  }
}
