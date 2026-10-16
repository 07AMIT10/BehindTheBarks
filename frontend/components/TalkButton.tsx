"use client";

import type { TalkControl } from "@/lib/useTalk";

const focusRing = "focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-[#7FE3E8]";

function MicIcon({ talking }: { talking: boolean }) {
  if (talking) {
    return (
      <svg
        width="13"
        height="13"
        viewBox="0 0 24 24"
        fill="currentColor"
        stroke="currentColor"
        strokeWidth="1.5"
        strokeLinecap="round"
        strokeLinejoin="round"
        aria-hidden="true"
        className="animate-pulse text-[#EF4444]"
      >
        <path d="M12 2a3 3 0 0 0-3 3v7a3 3 0 0 0 6 0V5a3 3 0 0 0-3-3Z" />
        <path d="M19 10v2a7 7 0 0 1-14 0v-2" />
        <line x1="12" x2="12" y1="19" y2="22" />
      </svg>
    );
  }
  return (
    <svg
      width="13"
      height="13"
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="2"
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
    >
      <path d="M12 2a3 3 0 0 0-3 3v7a3 3 0 0 0 6 0V5a3 3 0 0 0-3-3Z" />
      <path d="M19 10v2a7 7 0 0 1-14 0v-2" />
      <line x1="12" x2="12" y1="19" y2="22" />
    </svg>
  );
}

export function TalkButton({
  control,
  variant,
  disabled = false,
}: {
  control: TalkControl;
  variant: "bar" | "sheet";
  disabled?: boolean;
}) {
  const talking = control.talking;
  const name = "Talk to your pet";
  const tooltip = talking ? "Release to stop speaking" : "Hold to speak through station speaker";

  const talkingStyle = {
    background: "rgba(239, 68, 68, 0.22)",
    color: "#FCA5A5",
    border: "1px solid rgba(239, 68, 68, 0.5)",
  };
  const idleStyle = { color: "#D6D8DB" };

  const base =
    variant === "bar"
      ? `flex h-11 md:h-8 select-none items-center gap-1.5 rounded-sm px-2 sm:px-2.5 text-[12px] font-semibold transition-all active:scale-95 cursor-pointer ${focusRing}`
      : `flex h-11 w-full select-none items-center justify-center gap-2 rounded-xl px-3 text-[14px] font-semibold ring-1 ring-white/10 active:scale-95 cursor-pointer ${focusRing}`;

  return (
    <button
      type="button"
      aria-pressed={talking}
      aria-label={name}
      disabled={disabled}
      title={disabled ? "Push-to-talk unavailable" : tooltip}
      onPointerDown={(e) => {
        if (disabled) return;
        e.currentTarget.setPointerCapture?.(e.pointerId);
        void control.startTalk();
      }}
      onPointerUp={(e) => {
        if (disabled) return;
        try {
          e.currentTarget.releasePointerCapture?.(e.pointerId);
        } catch {}
        control.stopTalk();
      }}
      onPointerCancel={() => {
        control.stopTalk();
      }}
      onKeyDown={(e) => {
        if (disabled || e.repeat) return;
        if (e.key === " " || e.key === "Enter") {
          e.preventDefault();
          void control.startTalk();
        }
      }}
      onKeyUp={(e) => {
        if (disabled) return;
        if (e.key === " " || e.key === "Enter") {
          e.preventDefault();
          control.stopTalk();
        }
      }}
      className={`${base} ${disabled ? "cursor-not-allowed opacity-50" : ""}`}
      style={talking ? talkingStyle : idleStyle}
    >
      <MicIcon talking={talking} />
      <span>{talking ? "Speaking…" : "Hold to Talk"}</span>
    </button>
  );
}
