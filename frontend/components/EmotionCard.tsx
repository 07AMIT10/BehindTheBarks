import type { Emotion, EmotionState } from "@/lib/contracts";
import { EMOTION_META, emotionVars, sourceLabel } from "@/lib/emotions";
import { agoLabel, hhmm, hhmmss, pct, sinceLabel } from "@/lib/format";
import EmotionIcon from "./EmotionIcon";

type Props = {
  current: EmotionState | null;
  currentSince: number | null;
  lastEmotionAt: number | null;
  nowMs: number;
  nowSec: number;
  paused: boolean;
  projector: boolean;
  dogName: string;
  lastDogTs: number | null;
  lastSeenEmotion: Emotion | null;
};

function SourceBadge({ source }: { source: EmotionState["source"] }) {
  const label = sourceLabel(source);
  const rules = source === "rules";
  return (
    <div className={`flex h-[26px] items-center gap-1.5 rounded-full px-2.5 text-[12px] font-bold ${rules ? "bg-surface-2 text-text-soft" : "bg-accent-soft text-accent-ink"}`}>
      {label}
    </div>
  );
}

export default function EmotionCard(p: Props) {
  const sizes = p.projector
    ? { label: "text-display-xl", tile: 128, glyph: 80, reason: "text-[22px]" }
    : { label: "text-display-sm lg:text-display", tile: 88, glyph: 56, reason: "text-body lg:text-[17px]" };

  if (!p.current) {
    return (
      <section aria-label="Current emotion" className="relative flex flex-col gap-4 overflow-hidden rounded-xl border border-border bg-surface p-7">
        <div className="flex items-center gap-5">
          <div className="flex shrink-0 items-center justify-center rounded-[18px]" style={{ width: 72, height: 72, background: "var(--unknown-tint)" }}>
            <EmotionIcon emotion="unknown" size={44} strokeWidth={1.6} />
          </div>
          <div className="flex flex-col gap-1">
            <div className="text-title font-bold tracking-[-0.015em]">Waiting for {p.dogName}</div>
            <div className="text-small text-muted">Readings start when a dog enters the feeding zone.</div>
          </div>
        </div>
      </section>
    );
  }

  const c = p.current;
  const noDog = c.emotion === "unknown" && c.reason.startsWith("No dog");
  const v = emotionVars(c.emotion);
  const updated = p.lastEmotionAt ? agoLabel((p.nowMs - p.lastEmotionAt) / 1000) : "";

  return (
    <section aria-label="Current emotion" className="relative flex flex-col gap-[18px] overflow-hidden rounded-xl border border-border bg-surface p-6 lg:p-7">
      <div className="absolute inset-x-0 top-0 h-1" style={{ background: p.paused ? "var(--unknown)" : v.solid }} />
      <div className="flex items-center gap-2.5">
        <div className="micro text-muted">{p.paused ? "Paused · last reading" : "Right now"}</div>
        <div className="grow" />
        {!p.paused && !noDog && <SourceBadge source={c.source} />}
        {!p.paused && <div className="font-mono text-[12px] text-muted">updated {updated}</div>}
      </div>

      <div className={`flex items-center gap-5 ${p.paused ? "opacity-55" : ""}`}>
        <div className="flex shrink-0 items-center justify-center rounded-[18px]"
          style={{ width: sizes.tile, height: sizes.tile, background: noDog ? "var(--unknown-tint)" : v.tint }}>
          <EmotionIcon emotion={c.emotion} size={sizes.glyph} strokeWidth={1.6} />
        </div>
        <div className="flex min-w-0 flex-col gap-1">
          <div className={`${noDog ? "text-title" : sizes.label} font-bold tracking-[-0.025em]`} style={{ color: p.paused ? "var(--muted)" : v.fg }}>
            {noDog ? "No dog in view" : EMOTION_META[c.emotion].label}
          </div>
          <div className="flex items-center gap-1.5 text-[14px] text-muted">
            {p.paused ? (
              <>Held since {hhmmss(c.ts)}</>
            ) : noDog ? (
              p.lastDogTs ? (
                <>Last seen {hhmm(p.lastDogTs)}{p.lastSeenEmotion && (
                  <> · <EmotionIcon emotion={p.lastSeenEmotion} size={16} /> <span style={{ color: `var(--${p.lastSeenEmotion}-fg)` }}>{EMOTION_META[p.lastSeenEmotion].label}</span></>
                )}</>
              ) : <>Not seen yet this session</>
            ) : (
              p.currentSince !== null && sinceLabel(p.currentSince, p.nowSec)
            )}
          </div>
        </div>
      </div>

      {!noDog && (
        <div className={`flex flex-col gap-2 ${p.paused ? "opacity-55" : ""}`}>
          <div className="flex justify-between text-small">
            <span className="font-semibold text-muted">Confidence</span>
            <span className="font-mono font-medium">{pct(c.confidence)}</span>
          </div>
          <div role="meter" aria-label="Confidence" aria-valuenow={Math.round(c.confidence * 100)} aria-valuemin={0} aria-valuemax={100}
            className="h-2 overflow-hidden rounded bg-track">
            <div className="h-full rounded" style={{ width: pct(c.confidence), background: p.paused ? "#BDBFC3" : v.solid }} />
          </div>
        </div>
      )}

      {!noDog && <p className={`m-0 leading-[1.45] text-pretty ${sizes.reason} ${p.paused ? "opacity-55" : ""}`}>{c.reason}</p>}
    </section>
  );
}
