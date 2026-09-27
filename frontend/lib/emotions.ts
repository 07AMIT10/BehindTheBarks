import type { Emotion } from "./contracts";

export const EMOTION_ORDER: Emotion[] = ["happy", "excited", "relaxed", "anxious", "fearful", "aggressive", "disinterested", "unknown"];

export const FACE_CIRCLE = "M21 12a9 9 0 1 1-18 0a9 9 0 0 1 18 0z";
const EYE_L = "M8.6 10a.4.4 0 1 0 .8 0a.4.4 0 1 0 -.8 0";
const EYE_R = "M14.6 10a.4.4 0 1 0 .8 0a.4.4 0 1 0 -.8 0";

// Icon paths copied from ui/screens/00-dashboard-desktop.html (EMO table).
export const EMOTION_META: Record<Emotion, { label: string; paths: [string, string, string] }> = {
  happy: { label: "Happy", paths: [EYE_L, EYE_R, "M8 14c1 1.6 2.4 2.4 4 2.4s3-.8 4-2.4"] },
  excited: { label: "Excited", paths: ["M7.5 10c.5-1 2.5-1 3 0", "M13.5 10c.5-1 2.5-1 3 0", "M8 13.5h8c0 2.2-1.8 4-4 4s-4-1.8-4-4z"] },
  relaxed: { label: "Relaxed", paths: ["M7.5 10c.5 1 2.5 1 3 0", "M13.5 10c.5 1 2.5 1 3 0", "M9 15c.8.8 1.8 1.2 3 1.2s2.2-.4 3-1.2"] },
  anxious: { label: "Anxious", paths: [`${EYE_L} ${EYE_R}`, "M7.5 8.5l2.5-1M16.5 8.5l-2.5-1", "M8 16c.7-.8 1.3-.8 2 0s1.3.8 2 0 1.3-.8 2 0 1.3.8 2 0"] },
  fearful: { label: "Fearful", paths: ["M8.6 10.5a.4.4 0 1 0 .8 0a.4.4 0 1 0 -.8 0M14.6 10.5a.4.4 0 1 0 .8 0a.4.4 0 1 0 -.8 0", "M7.5 7.5c.7-.7 2-.7 2.5 0M14 7.5c.5-.7 1.8-.7 2.5 0", "M13.5 16a1.5 1.5 0 1 1-3 0a1.5 1.5 0 0 1 3 0z"] },
  aggressive: { label: "Aggressive", paths: ["M8.6 11a.4.4 0 1 0 .8 0a.4.4 0 1 0 -.8 0M14.6 11a.4.4 0 1 0 .8 0a.4.4 0 1 0 -.8 0", "M7.5 8l2.5 1.5M16.5 8l-2.5 1.5", "M8.5 16.2c1-.8 2.2-1.2 3.5-1.2s2.5.4 3.5 1.2"] },
  disinterested: { label: "Disinterested", paths: ["M8 10.5h2.5M13.5 10.5H16", "", "M9.5 15.5h5"] },
  unknown: { label: "Unknown", paths: ["M9.8 9.5a2.3 2.3 0 1 1 3.3 2.1c-.7.3-1.1.9-1.1 1.6v.3", "M12 16.5h.01", ""] },
};

export const NEGATIVE: ReadonlySet<Emotion> = new Set<Emotion>(["anxious", "fearful", "aggressive", "disinterested"]);

export function sourceLabel(s: "rules" | "llm" | "fused"): "Rules" | "AI" | "Fused" {
  return s === "llm" ? "AI" : s === "fused" ? "Fused" : "Rules";
}

export function emotionVars(e: Emotion): { solid: string; fg: string; tint: string } {
  return { solid: `var(--${e})`, fg: `var(--${e}-fg)`, tint: `var(--${e}-tint)` };
}
