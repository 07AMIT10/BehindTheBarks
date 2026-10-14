"use client";

import { useRef, useState } from "react";
import { useDialog } from "@/lib/useDialog";

type CueOption = {
  id: string;
  label: string;
  desc: string;
  icon: string;
  sound: string;
  light?: string;
  color: string;
};

const CUES: CueOption[] = [
  {
    id: "squeak-flash",
    label: "Squeak + Beacon",
    desc: "Attention squeak + 2 flash pulses",
    icon: "✨",
    sound: "squeak",
    light: "double_blink",
    color: "#F59E0B",
  },
  {
    id: "squeak",
    label: "Squeaky Toy",
    desc: "High-pitch attention squeak",
    icon: "🧸",
    sound: "squeak",
    color: "#EC4899",
  },
  {
    id: "whistle",
    label: "Dog Whistle",
    desc: "Friendly focus chime",
    icon: "🎵",
    sound: "whistle",
    color: "#3B82F6",
  },
  {
    id: "doorbell",
    label: "Doorbell Chime",
    desc: "Alert ding-dong chime",
    icon: "🔔",
    sound: "doorbell",
    color: "#10B981",
  },
  {
    id: "lullaby",
    label: "Calm Lullaby",
    desc: "Gentle soothing harmonic tone",
    icon: "🎶",
    sound: "lullaby",
    color: "#8B5CF6",
  },
  {
    id: "beacon-only",
    label: "Silent Light Pulse",
    desc: "Gentle 3-pulse beacon (no sound)",
    icon: "💡",
    sound: "none",
    light: "beacon",
    color: "#FBBF24",
  },
];

type Props = {
  isOpen: boolean;
  onClose: () => void;
  http: string;
  token?: string | null;
  stationId?: string;
  dogName: string;
};

export default function SoundboardDrawer({
  isOpen,
  onClose,
  http,
  token,
  stationId,
  dogName,
}: Props) {
  const rootRef = useRef<HTMLDivElement>(null);
  useDialog(rootRef, isOpen, onClose);
  const [activeCue, setActiveCue] = useState<string | null>(null);
  const [feedback, setFeedback] = useState<string | null>(null);

  if (!isOpen) return null;

  const triggerCue = async (cue: CueOption) => {
    setActiveCue(cue.id);
    setFeedback(`Sent ${cue.label} to ${dogName}'s station`);
    try {
      const tokenParam = token ? `?token=${encodeURIComponent(token)}` : "";
      const res = await fetch(`${http}/cue${tokenParam}`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          sound: cue.sound,
          light: cue.light,
          station: stationId,
        }),
      });
      const data = await res.json();
      if (!data.sent) {
        setFeedback("Station phone is currently offline");
      }
    } catch {
      setFeedback("Failed to trigger cue");
    } finally {
      setTimeout(() => setActiveCue(null), 800);
      setTimeout(() => setFeedback(null), 3000);
    }
  };

  return (
    <div
      className="fixed inset-0 z-50 flex items-end justify-center bg-black/60 p-0 sm:items-center sm:p-4 backdrop-blur-xs"
      onClick={(e) => {
        if (e.target === e.currentTarget) onClose();
      }}
      role="presentation"
    >
      <div
        ref={rootRef}
        role="dialog"
        aria-modal="true"
        aria-labelledby="soundboard-drawer-title"
        tabIndex={-1}
        className="relative flex w-full max-w-lg flex-col overflow-hidden rounded-t-2xl border-t border-border bg-surface sm:rounded-2xl sm:border p-4 shadow-2xl focus:outline-none"
        style={{ paddingBottom: "max(16px, env(safe-area-inset-bottom))" }}
      >
        {/* Header */}
        <div className="flex items-center justify-between border-b border-border/60 pb-3">
          <div className="flex items-center gap-2">
            <span className="text-xl">🐾</span>
            <div>
              <h2 id="soundboard-drawer-title" className="text-base font-bold text-text">
                Soundboard & Cues for {dogName}
              </h2>
              <div className="text-xs text-muted">Multi-sensory acoustic and light attention cues</div>
            </div>
          </div>
          <button
            type="button"
            onClick={onClose}
            aria-label="Close soundboard"
            className="flex h-9 w-9 items-center justify-center rounded-lg text-muted hover:bg-surface-2 hover:text-text"
          >
            <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
              <line x1="18" y1="6" x2="6" y2="18" />
              <line x1="6" y1="6" x2="18" y2="18" />
            </svg>
          </button>
        </div>

        {/* Feedback pill */}
        {feedback && (
          <div className="mt-3 flex items-center justify-center rounded-lg bg-surface-2 px-3 py-1.5 text-xs font-semibold text-accent animate-fade-in">
            {feedback}
          </div>
        )}

        {/* Cues Grid */}
        <div className="mt-4 grid grid-cols-2 gap-2.5 sm:grid-cols-2">
          {CUES.map((cue) => {
            const isTriggered = activeCue === cue.id;
            return (
              <button
                key={cue.id}
                type="button"
                onClick={() => triggerCue(cue)}
                className={`flex flex-col items-start rounded-xl border p-3 text-left transition-all active:scale-95 ${
                  isTriggered
                    ? "border-accent bg-accent/15 scale-98"
                    : "border-border/80 bg-surface-2/40 hover:bg-surface-2 hover:border-border"
                }`}
              >
                <div className="flex w-full items-center justify-between">
                  <span className="text-2xl">{cue.icon}</span>
                  {cue.light && (
                    <span className="rounded bg-amber-400/20 px-1.5 py-0.5 text-[10px] font-bold text-amber-300">
                      ⚡ Flash
                    </span>
                  )}
                </div>
                <div className="mt-2 text-sm font-bold text-text">{cue.label}</div>
                <div className="text-[11px] text-muted line-clamp-1">{cue.desc}</div>
              </button>
            );
          })}
        </div>

        {/* Footer */}
        <div className="mt-4 flex items-center justify-between border-t border-border/60 pt-3 text-xs text-muted">
          <span>Plays on station phone speaker & flash</span>
          <button
            type="button"
            onClick={onClose}
            className="rounded-lg bg-surface-2 px-3 py-1.5 font-semibold text-text hover:bg-border transition-colors"
          >
            Close
          </button>
        </div>
      </div>
    </div>
  );
}
