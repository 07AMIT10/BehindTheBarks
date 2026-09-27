import type { Emotion } from "@/lib/contracts";
import { EMOTION_META, FACE_CIRCLE } from "@/lib/emotions";

type Props = { emotion: Emotion; size?: number; strokeWidth?: number; color?: string };

export default function EmotionIcon({ emotion, size = 24, strokeWidth = 1.8, color }: Props) {
  const paths = EMOTION_META[emotion].paths.filter(Boolean);
  return (
    <svg width={size} height={size} viewBox="0 0 24 24" fill="none" stroke={color ?? `var(--${emotion}-fg)`}
      strokeWidth={strokeWidth} strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
      <path d={FACE_CIRCLE} strokeDasharray={emotion === "unknown" ? "2.2 2.4" : undefined} />
      {paths.map((d, i) => <path key={i} d={d} />)}
    </svg>
  );
}
