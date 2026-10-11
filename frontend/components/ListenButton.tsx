"use client";

import type { ListenControl } from "@/lib/useListen";

// The Listen controls for the video panel. Dark overlay styling like the other video controls (the
// video area is always dark, in both themes). Hidden when audio is not offered at all, locked (lock
// icon, tooltip) when the viewer's ceiling is below Full.

const LOCKED_TEXT = "Audio is only available in Full mode";
const scrim = { background: "var(--overlay-scrim)" };
const focusRing = "focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-[#7FE3E8]";

function Icon({ locked, listening }: { locked: boolean; listening: boolean }) {
  if (locked) {
    return (
      <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
        <rect x="3" y="11" width="18" height="11" rx="2" ry="2" /><path d="M7 11V7a5 5 0 0 1 10 0v4" />
      </svg>
    );
  }
  return (
    <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
      <path d="M11 5L6 9H2v6h4l5 4V5z" />
      {listening ? <path d="M15.5 8.5a5 5 0 0 1 0 7M19 5a10 10 0 0 1 0 14" /> : <path d="M23 9l-6 6M17 9l6 6" />}
    </svg>
  );
}

/** The toggle. variant "bar" is the desktop controls group (44 px below md, 32 px at md+); "sheet" is the phone bottom sheet. */
export function ListenButton({ control, variant }: { control: ListenControl; variant: "bar" | "sheet" }) {
  if (control.availability === "hidden") return null;
  const locked = control.availability === "locked";
  const pressed = control.listening;
  const name = pressed ? "Stop listening" : "Listen to the room";
  const pressedStyle = { background: "rgba(127,227,232,.18)", color: "#BFF2F4" };
  const idleStyle = { color: "#D6D8DB" };
  const base =
    variant === "bar"
      ? `flex h-11 md:h-8 items-center gap-1.5 rounded-sm px-2 sm:px-2.5 text-[12px] font-semibold transition-all active:scale-95 ${focusRing}`
      : `flex h-11 w-full items-center justify-center gap-2 rounded-xl px-3 text-[14px] font-semibold ring-1 ring-white/10 active:scale-95 ${focusRing}`;
  return (
    <button
      type="button"
      aria-pressed={pressed}
      aria-label={name}
      aria-disabled={locked || undefined}
      title={locked ? LOCKED_TEXT : name}
      onClick={() => {
        if (!locked) control.toggle();
      }}
      className={`${base} ${locked ? "cursor-not-allowed opacity-60" : ""}`}
      style={pressed ? pressedStyle : locked ? { color: "#A0A5AD" } : idleStyle}
    >
      <Icon locked={locked} listening={pressed} />
      <span>{pressed ? "Stop" : "Listen"}</span>
    </button>
  );
}

/** Small level meter for the bottom bar. role=meter with aria-valuenow; the number is also in aria-valuetext. */
export function ListenMeter({ level }: { level: number }) {
  return (
    <div
      role="meter"
      aria-label="Audio level"
      aria-valuemin={0}
      aria-valuemax={100}
      aria-valuenow={level}
      aria-valuetext={`${level}%`}
      className="h-1.5 w-12 overflow-hidden rounded-full bg-white/15"
    >
      <div className="h-full rounded-full bg-[#7FE3E8] transition-[width] duration-150" style={{ width: `${level}%` }} />
    </div>
  );
}

/** Status line: "Connecting...", "Listening" with the meter, or the error text. Nothing when idle. */
export function ListenStatus({ control }: { control: ListenControl }) {
  if (control.availability === "hidden" || !control.statusText) return null;
  const isError = control.failed;
  return (
    <div className="flex h-11 md:h-8 items-center gap-2 rounded-lg px-3 text-[12px]" style={scrim}>
      {control.listening && <ListenMeter level={control.stats?.level ?? 0} />}
      <p role="status" aria-live="polite" className={isError ? "text-[#FCD34D]" : "text-[#D6D8DB]"}>
        {control.statusText}
      </p>
    </div>
  );
}
