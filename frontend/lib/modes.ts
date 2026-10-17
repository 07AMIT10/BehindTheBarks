import type { ModeRequestResult, ViewMode } from "./types";

export const MODE_ORDER: readonly ViewMode[] = ["blackout", "privacy", "limited", "full"] as const;

export interface ModeDefinition {
  mode: ViewMode;
  label: string;
  desc: string;
  icon: string;
  enabled: boolean;
}

/**
 * Configurable viewing mode definitions and presentation sequence.
 * Modify order or enabled status here to change what is displayed.
 * Full is placed first (recommended) for pet monitoring.
 */
export const CONFIGURABLE_MODES: readonly ModeDefinition[] = [
  {
    mode: "full",
    label: "Full",
    desc: "Live video and sound (Recommended)",
    icon: "👁️",
    enabled: true,
  },
  {
    mode: "limited",
    label: "Limited",
    desc: "Blurred snapshots every 10 s",
    icon: "🖼️",
    enabled: true,
  },
  {
    mode: "privacy",
    label: "Privacy",
    desc: "Barks and behaviour only (No video)",
    icon: "🔇",
    enabled: true,
  },
  {
    mode: "blackout",
    label: "Blackout",
    desc: "Nothing leaves the phone",
    icon: "⛔",
    enabled: true,
  },
] as const;

export const DISPLAY_MODES: readonly ViewMode[] = CONFIGURABLE_MODES
  .filter((m) => m.enabled)
  .map((m) => m.mode);

export const MODE_METADATA: Record<ViewMode, { label: string; desc: string; icon: string }> = {
  blackout: {
    label: "Blackout",
    desc: "Nothing leaves the phone",
    icon: "⛔",
  },
  privacy: {
    label: "Privacy",
    desc: "Barks and behaviour only",
    icon: "🔇",
  },
  limited: {
    label: "Limited",
    desc: "Blurred snapshots every 10 s",
    icon: "🖼️",
  },
  full: {
    label: "Full",
    desc: "Live video and sound",
    icon: "👁️",
  },
};

/** What the dashboard says once the phone answered a raise prompt (or never did). */
export function modeResultLabel(result: ModeRequestResult["result"]): string {
  switch (result) {
    case "allowed":
      return "Request approved on the phone";
    case "denied":
      return "Request declined on the phone";
    case "timeout":
      return "No answer on the phone";
  }
}

/** Compact version for the mode sheet's pending notice: "Station: <answer>". */
export function stationAnswerLabel(result: ModeRequestResult["result"]): string {
  switch (result) {
    case "allowed":
      return "accepted";
    case "denied":
      return "rejected";
    case "timeout":
      return "no answer";
  }
}

export function modeRank(mode: ViewMode): number {
  const idx = MODE_ORDER.indexOf(mode);
  return idx === -1 ? 0 : idx;
}

export function minMode(...modes: (ViewMode | undefined | null)[]): ViewMode {
  let min: ViewMode = "full";
  for (const m of modes) {
    if (m && modeRank(m) < modeRank(min)) {
      min = m;
    }
  }
  return min;
}

export function isModeAllowed(mode: ViewMode, ceiling: ViewMode = "full"): boolean {
  return modeRank(mode) <= modeRank(ceiling);
}
