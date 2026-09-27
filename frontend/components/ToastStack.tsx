"use client";

import { hhmm } from "@/lib/format";
import type { Toast } from "@/lib/toasts";
import EmotionIcon from "./EmotionIcon";

type Props = { toasts: Toast[]; onDismiss: (id: string) => void; onView: (ts: number) => void };

function statusLine(t: Toast): string | null {
  if (t.status === "dashboard_only") return "Would send to owner";
  if (t.status === "failed") return "Telegram failed";
  if (t.status === "sent") return "Sent to owner";
  return null;
}

export default function ToastStack({ toasts, onDismiss, onView }: Props) {
  if (!toasts.length) return null;
  return (
    <div className="pointer-events-none fixed inset-x-3 top-3 z-30 flex flex-col gap-3 lg:inset-x-auto lg:right-8 lg:top-8 lg:w-[380px]">
      {toasts.map((t) => {
        const words = t.title.split(" ");
        const last = words.pop();
        const line = statusLine(t);
        return (
          <div key={t.id} role={t.kind === "negative" ? "alert" : "status"}
            className="pointer-events-auto relative overflow-hidden rounded-lg border border-border bg-surface shadow-[var(--shadow-toast)]">
            {t.emotion && <div className="absolute inset-x-0 top-0 h-[3px]" style={{ background: `var(--${t.emotion})` }} />}
            <div className="flex gap-3 p-4">
              <div className="flex h-10 w-10 shrink-0 items-center justify-center rounded-md"
                style={{ background: t.emotion ? `var(--${t.emotion}-tint)` : "var(--accent-soft)" }}>
                {t.emotion ? <EmotionIcon emotion={t.emotion} size={24} /> : (
                  <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="var(--accent)" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true"><path d="M12 3l2.5 6.5L21 12l-6.5 2.5L12 21l-2.5-6.5L3 12l6.5-2.5z" /></svg>
                )}
              </div>
              <div className="flex min-w-0 grow flex-col gap-1">
                <div className="flex items-baseline gap-2">
                  <div className="min-w-0 grow truncate text-[14px] font-bold">
                    {t.emotion ? <>{words.join(" ")} <span style={{ color: `var(--${t.emotion}-fg)` }}>{last}</span></> : t.title}
                    {t.count > 1 && <span className="ml-1.5 rounded-full bg-surface-2 px-1.5 text-[11px] font-semibold text-muted">×{t.count}</span>}
                  </div>
                  <div className="font-mono text-[12px] text-muted">{hhmm(t.ts)}</div>
                </div>
                {t.body && <div className="line-clamp-2 text-small text-text-soft">{t.body}</div>}
                {line && <div className="text-[12px] text-muted" title={t.detail}>{line}</div>}
                {t.kind === "negative" && (
                  <div className="mt-1.5 flex gap-2">
                    <button type="button" onClick={() => onView(t.ts)} className="h-8 rounded-sm bg-accent px-3 text-[12px] font-semibold text-accent-fg">View moment</button>
                    <button type="button" onClick={() => onDismiss(t.id)} className="h-8 rounded-sm border border-border px-3 text-[12px] font-semibold text-text">Dismiss</button>
                  </div>
                )}
              </div>
              {t.kind !== "negative" && (
                <button type="button" aria-label="Dismiss" onClick={() => onDismiss(t.id)} className="-m-2 flex h-11 w-11 shrink-0 items-center justify-center text-muted">
                  <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" aria-hidden="true"><path d="M6 6l12 12M18 6L6 18" /></svg>
                </button>
              )}
            </div>
          </div>
        );
      })}
    </div>
  );
}
