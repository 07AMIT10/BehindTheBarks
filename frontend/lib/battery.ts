/**
 * Battery and telemetry helpers (T6.2)
 */

export type BatteryAlertLevel = "low" | "critical";

export const STALE_TELEMETRY_THRESHOLD_S = 45;

/**
 * Returns true if the last telemetry timestamp or age is older than thresholdSec (default 45s).
 * If lastSampleTsOrAge is an age (e.g. from last_frame_age_s) it is evaluated directly against thresholdSec.
 */
export function isTelemetryStale(
  lastSampleTsOrAge: number | null | undefined,
  nowSec?: number,
  thresholdSec: number = STALE_TELEMETRY_THRESHOLD_S
): boolean {
  if (lastSampleTsOrAge === null || lastSampleTsOrAge === undefined || lastSampleTsOrAge < 0) {
    return true;
  }
  if (nowSec !== undefined) {
    return nowSec - lastSampleTsOrAge > thresholdSec;
  }
  // When passed age directly (small number), compare directly to threshold
  if (lastSampleTsOrAge < 100000) {
    return lastSampleTsOrAge > thresholdSec;
  }
  return (Date.now() / 1000) - lastSampleTsOrAge > thresholdSec;
}

/**
 * Returns formatted battery percentage string, or '?' if null, undefined, or stale.
 */
export function batteryDisplay(
  pct: number | null | undefined,
  isStale: boolean = false
): string {
  if (isStale || pct === null || pct === undefined || pct < 0) {
    return "?";
  }
  return `${Math.round(pct)}%`;
}

/**
 * Returns CSS style class name or color identifier for battery level.
 * - stale / unknown: muted
 * - <= 10%: critical (red)
 * - <= 20%: low (amber)
 * - > 20%: normal
 */
export function batteryLevelClass(
  pct: number | null | undefined,
  isStale: boolean = false
): "critical" | "low" | "normal" | "unknown" {
  if (isStale || pct === null || pct === undefined || pct < 0) {
    return "unknown";
  }
  if (pct <= 10) {
    return "critical";
  }
  if (pct <= 20) {
    return "low";
  }
  return "normal";
}

/**
 * Evaluates whether a battery level crossing triggers a toast alert.
 * Alerts trigger at 20% and 10% when not charging (once per crossing).
 * Resets when charging or when battery rises above 25%.
 */
export function shouldToast(
  prevPct: number | null | undefined,
  nextPct: number | null | undefined,
  charging: boolean
): BatteryAlertLevel | null {
  if (charging) {
    return null;
  }
  if (nextPct === null || nextPct === undefined || nextPct < 0) {
    return null;
  }

  // First sample evaluation if no previous sample
  if (prevPct === null || prevPct === undefined || prevPct < 0) {
    if (nextPct <= 10) return "critical";
    if (nextPct <= 20) return "low";
    return null;
  }

  // Downward crossing into critical (<= 10%)
  if (prevPct > 10 && nextPct <= 10) {
    return "critical";
  }

  // Downward crossing into low (<= 20% and > 10%)
  if (prevPct > 20 && nextPct <= 20 && nextPct > 10) {
    return "low";
  }

  return null;
}
