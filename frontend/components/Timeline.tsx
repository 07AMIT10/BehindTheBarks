"use client";

import { EMOTION_META, EMOTION_ORDER, emotionVars, sourceLabel } from "@/lib/emotions";
import { durationLabel, hhmm, pct } from "@/lib/format";
import { groupAudio, layoutSpans, leftPct, ticks, timelineWindow } from "@/lib/timeline";
import type { AudioMark, NotificationItem, Span } from "@/lib/types";
import EmotionIcon from "./EmotionIcon";

type Props = {
  spans: Span[];
  audio: AudioMark[];
  treats: number[];
  notifications: NotificationItem[];
  nowSec: number;
  selected: number | null;
  onSelect: (index: number) => void;
};

const TREAT_ICON = "M3 13h18a9 5 0 0 1-18 0zM12 3v6";
const BELL_ICON = "M6 16V11a6 6 0 0 1 12 0v5l1.5 2h-15zM10 20.5a2 2 0 0 0 4 0";

export default function Timeline({ spans, audio, treats, notifications, nowSec, selected, onSelect }: Props) {
  const w = timelineWindow(spans, nowSec);
  const laid = layoutSpans(spans, w, nowSec);
  const sounds = groupAudio(audio, w);
  const tickList = ticks(w, 6);
  const markers = [
    ...treats.filter((t) => t >= w.start).map((t) => ({ t, kind: "treat" as const, title: `Treat dropped ${hhmm(t)}` })),
    ...notifications.filter((n) => n.ts >= w.start).map((n) => ({ t: n.ts, kind: "bell" as const, title: `Owner notified ${hhmm(n.ts)}` })),
  ];
  const selIndex = selected ?? spans.length - 1;
  const sel = spans[selIndex];

  return (
    <section aria-label="Session timeline" className="flex flex-col gap-3.5 rounded-xl border border-border bg-surface px-4 py-5 lg:px-6">
      <div className="flex flex-wrap items-center gap-4">
        <div className="text-heading font-semibold"><span className="lg:hidden">Timeline</span><span className="hidden lg:inline">Session timeline</span></div>
        <div className="font-mono text-small text-muted">{hhmm(w.start)} – now · {durationLabel(w.end - w.start)}</div>
        <div className="grow" />
        <div className="text-[12px] text-muted lg:hidden">Swipe</div>
        <div className="hidden flex-wrap gap-3.5 lg:flex">
          {EMOTION_ORDER.map((e) => (
            <div key={e} className="flex items-center gap-1.5 text-[12px] text-muted">
              <EmotionIcon emotion={e} size={16} strokeWidth={1.9} />{EMOTION_META[e].label}
            </div>
          ))}
        </div>
      </div>

      {spans.length === 0 ? (
        <div className="flex flex-col items-center gap-1 rounded-lg border border-dashed border-border px-6 py-8 text-center">
          <div className="text-body font-semibold">The timeline fills in as readings arrive</div>
          <div className="text-small text-muted">
            Press <span className="font-bold text-accent-ink">Treat dropped</span> to mark a moment and watch the response.
          </div>
        </div>
      ) : (
        <div className="-mx-4 overflow-x-auto px-4 lg:mx-0 lg:px-0">
          <div className="grid min-w-[640px] grid-cols-[72px_minmax(0,1fr)] gap-x-3 lg:min-w-0">
            <div className="flex flex-col text-[12px] font-semibold text-muted">
              <div className="h-6" />
              <div className="flex h-11 items-center">Emotion</div>
              <div className="h-2" />
              <div className="flex h-10 items-center">Audio</div>
            </div>
            <div className="relative">
              <div className="pointer-events-none absolute inset-x-0 bottom-5 top-6">
                {markers.map((m, i) => (
                  <div key={`l${i}`} className="absolute bottom-0 top-0 border-l border-dashed opacity-70"
                    style={{ left: `${leftPct(m.t, w)}%`, borderColor: m.kind === "treat" ? "var(--accent)" : "var(--muted)" }} />
                ))}
              </div>
              <div className="relative h-6">
                {markers.map((m, i) => (
                  <div key={`m${i}`} title={m.title}
                    className="absolute top-0 flex h-5 w-5 -translate-x-1/2 items-center justify-center rounded-full"
                    style={{ left: `${leftPct(m.t, w)}%`, background: m.kind === "treat" ? "var(--accent-soft)" : "var(--surface-2)", color: m.kind === "treat" ? "var(--accent)" : "var(--muted)" }}>
                    <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.4" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
                      <path d={m.kind === "treat" ? TREAT_ICON : BELL_ICON} />
                    </svg>
                  </div>
                ))}
              </div>
              <div className="relative h-11 overflow-hidden rounded-lg bg-track">
                {laid.map(({ span, index, left, width }) => {
                  const v = emotionVars(span.emotion);
                  const end = index === spans.length - 1 ? "now" : hhmm(span.end);
                  return (
                    <button key={`${span.start}-${index}`} type="button" onClick={() => onSelect(index)}
                      aria-label={`${EMOTION_META[span.emotion].label}, ${hhmm(span.start)} to ${end}`}
                      className="absolute bottom-0 top-0 flex items-center justify-center overflow-hidden border-r-2 border-surface p-0"
                      style={{ left: `${left}%`, width: `${width}%`, background: v.tint, boxShadow: index === selIndex ? `inset 0 0 0 2px ${v.fg}` : "none" }}>
                      <span className="absolute inset-x-0 top-0 h-1" style={{ background: v.solid }} />
                      <EmotionIcon emotion={span.emotion} size={18} strokeWidth={1.9} />
                    </button>
                  );
                })}
              </div>
              <div className="h-2" />
              <div className="relative h-10 rounded-lg bg-surface-2">
                <div className="absolute inset-x-0 top-1/2 h-px bg-border" />
                {sounds.map((a, i) => (
                  <div key={`a${i}`}
                    className="absolute top-1/2 flex h-6 -translate-x-1/2 -translate-y-1/2 items-center gap-1 whitespace-nowrap rounded-full border border-border bg-surface px-2 text-[11px] font-semibold"
                    style={{ left: `${a.left}%` }}>
                    <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="var(--muted)" strokeWidth="2.2" strokeLinecap="round" aria-hidden="true"><path d="M4 10v4M8 7v10M12 4v16M16 8v8M20 10v4" /></svg>
                    {a.label}{a.count > 1 ? ` ×${a.count}` : ""}
                  </div>
                ))}
              </div>
              <div className="relative mt-1.5 h-5">
                {tickList.map((k, i) => (
                  <div key={`t${i}`} className="absolute top-0 font-mono text-[11px] text-muted"
                    style={{ left: `${k.left}%`, transform: k.align === "start" ? "none" : k.align === "end" ? "translateX(-100%)" : "translateX(-50%)" }}>
                    {k.label}
                  </div>
                ))}
              </div>
            </div>
          </div>
        </div>
      )}

      {sel && (
        <div className="flex flex-wrap items-center gap-x-3.5 gap-y-1 rounded-[12px] px-4 py-3" style={{ background: emotionVars(sel.emotion).tint }}>
          <EmotionIcon emotion={sel.emotion} size={24} />
          <div className="min-w-24 text-body font-bold" style={{ color: emotionVars(sel.emotion).fg }}>{EMOTION_META[sel.emotion].label}</div>
          <div className="whitespace-nowrap font-mono text-small text-text-soft">
            {hhmm(sel.start)} – {selIndex === spans.length - 1 ? "now" : hhmm(sel.end)}
          </div>
          <div className="min-w-0 grow text-[14px] text-text">{sel.reason}</div>
          <div className="whitespace-nowrap text-[12px] text-text-soft">{sourceLabel(sel.source)} · {pct(sel.confidence)}</div>
        </div>
      )}
    </section>
  );
}
