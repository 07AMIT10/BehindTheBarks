"use client";

import { useRef } from "react";
import type { Emotion } from "@/lib/contracts";
import { hhmm } from "@/lib/format";
import { useDialog } from "@/lib/useDialog";
import EmotionIcon from "./EmotionIcon";

export type MomentDetail = {
  emotion: Emotion;
  ts: number;
  reason: string;
  confidence?: number;
  source?: string;
  snapshot?: string | null;
  duration_s?: number;
};

type Props = {
  isOpen: boolean;
  onClose: () => void;
  dogName: string;
  moment: MomentDetail | null;
};

export default function MomentModal({ isOpen, onClose, dogName, moment }: Props) {
  const rootRef = useRef<HTMLDivElement>(null);
  useDialog(rootRef, isOpen, onClose);

  if (!isOpen || !moment) return null;

  const imgSrc = moment.snapshot
    ? moment.snapshot.startsWith("data:") || moment.snapshot.startsWith("http") || moment.snapshot.startsWith("/")
      ? moment.snapshot
      : `data:image/jpeg;base64,${moment.snapshot}`
    : null;

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/70 p-4 backdrop-blur-sm"
      onClick={(e) => {
        if (e.target === e.currentTarget) onClose();
      }}
      role="presentation"
    >
      <div
        ref={rootRef}
        role="dialog"
        aria-modal="true"
        aria-labelledby="moment-modal-title"
        tabIndex={-1}
        className="relative flex w-full max-w-lg flex-col overflow-hidden rounded-xl border border-border bg-surface shadow-2xl focus:outline-none"
      >
        {/* Color accent strip */}
        <div className="h-1.5 w-full" style={{ background: `var(--${moment.emotion})` }} />

        {/* Modal Header */}
        <div className="flex items-center justify-between border-b border-border/60 p-4">
          <div className="flex items-center gap-3">
            <div
              className="flex h-10 w-10 items-center justify-center rounded-lg"
              style={{ background: `var(--${moment.emotion}-tint)` }}
            >
              <EmotionIcon emotion={moment.emotion} size={24} />
            </div>
            <div>
              <h2 id="moment-modal-title" className="text-base font-bold text-text">
                {dogName} · <span className="capitalize" style={{ color: `var(--${moment.emotion}-fg)` }}>{moment.emotion}</span>
              </h2>
              <div className="font-mono text-xs text-muted">
                {hhmm(moment.ts)}
                {moment.duration_s ? ` · ${moment.duration_s.toFixed(1)}s duration` : ""}
              </div>
            </div>
          </div>
          <button
            type="button"
            onClick={onClose}
            aria-label="Close dialog"
            className="flex h-9 w-9 items-center justify-center rounded-lg text-muted hover:bg-surface-2 hover:text-text"
          >
            <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
              <line x1="18" y1="6" x2="6" y2="18" />
              <line x1="6" y1="6" x2="18" y2="18" />
            </svg>
          </button>
        </div>

        {/* Modal Body */}
        <div className="flex flex-col gap-4 p-4">
          {/* Snapshot or Fallback perception view */}
          <div className="relative aspect-video w-full overflow-hidden rounded-lg border border-border/80 bg-black/40 flex items-center justify-center">
            {imgSrc ? (
              // eslint-disable-next-line @next/next/no-img-element
              <img
                src={imgSrc}
                alt={`${dogName} ${moment.emotion} moment`}
                className="h-full w-full object-contain"
              />
            ) : (
              <div className="flex flex-col items-center gap-2 p-6 text-center">
                <div className="flex h-12 w-12 items-center justify-center rounded-full bg-surface-2 text-muted">
                  <svg width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.75" strokeLinecap="round" strokeLinejoin="round">
                    <path d="M23 19a2 2 0 0 1-2 2H3a2 2 0 0 1-2-2V8a2 2 0 0 1 2-2h4l2-3h6l2 3h4a2 2 0 0 1 2 2z" />
                    <circle cx="12" cy="13" r="4" />
                  </svg>
                </div>
                <div className="text-sm font-medium text-text">Optical Privacy Frame</div>
                <p className="max-w-xs text-xs text-muted">
                  Raw camera frame discarded to preserve home privacy. Perception state inferred via on-device keypoints.
                </p>
              </div>
            )}

            {/* Confidence tag overlay */}
            {typeof moment.confidence === "number" && (
              <div className="absolute bottom-2 left-2 rounded bg-black/70 px-2 py-0.5 font-mono text-[11px] text-white backdrop-blur-xs">
                {(moment.confidence * 100).toFixed(0)}% confidence · {moment.source ?? "fused"}
              </div>
            )}
          </div>

          {/* Explanation reason card */}
          <div className="rounded-lg border border-border/60 bg-surface-2/50 p-3">
            <div className="text-xs font-semibold text-muted">Perception Explanation</div>
            <div className="mt-1 text-sm text-text-soft leading-relaxed">{moment.reason}</div>
          </div>
        </div>

        {/* Modal Footer */}
        <div className="flex justify-end border-t border-border/60 bg-surface-2/20 p-3">
          <button
            type="button"
            onClick={onClose}
            className="rounded-lg bg-surface-2 px-4 py-2 text-sm font-semibold text-text hover:bg-border transition-colors"
          >
            Dismiss
          </button>
        </div>
      </div>
    </div>
  );
}
