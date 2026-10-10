import type { ViewMode } from "./types";

export const MODE_ORDER: readonly ViewMode[] = ["blackout", "privacy", "limited", "full"] as const;

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
