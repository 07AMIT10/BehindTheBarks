"use client";

import { useState } from "react";
import { hhmmss } from "@/lib/format";
import { phoneNotice } from "@/lib/phone";
import type { Status } from "@/lib/types";

type Props = {
  connected: boolean;
  everConnected: boolean;
  reconnectAttempt: number;
  status: Status | null;
  lastFrameTs: number | null;
  onDemo?: () => void;
};

const FIX_STEPS = [
  "Tap the site settings icon next to the address.",
  "Set Camera and Microphone to Allow.",
  "Come back and tap Try again.",
];

function Banner({ children }: { children: React.ReactNode }) {
  return <div role="status" className="flex flex-wrap items-center gap-x-3 gap-y-1 rounded-md border border-border bg-surface-2 px-4 py-2.5 text-small text-text-soft">{children}</div>;
}

function CameraBlocked({ status, onDemo }: { status: Status | null; onDemo?: () => void }) {
  const [showFix, setShowFix] = useState(false);
  const demoAvailable = Boolean(status?.modes?.includes("demo")) && Boolean(onDemo);
  return (
    <section role="status" className="flex flex-col items-center gap-3 rounded-lg border border-border bg-surface p-6 text-center">
      <div className="flex h-14 w-14 items-center justify-center rounded-2xl bg-surface-2 text-text-soft">
        <svg width="28" height="28" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.7" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
          <path d="M3 8a2 2 0 0 1 2-2h2l1.5-2h7L17 6h2a2 2 0 0 1 2 2v9a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2z" />
          <path d="M15 12.5a3 3 0 1 1-6 0a3 3 0 0 1 6 0z" />
          <path d="M3 3l18 18" />
        </svg>
      </div>
      <div className="text-[18px] font-bold">Camera blocked on the camera phone</div>
      <div className="max-w-[380px] text-[14px] leading-[1.45] text-muted">Open Claude Pet on that phone and allow camera access, or play a demo clip instead.</div>
      <div className="flex gap-2.5">
        <button type="button" aria-expanded={showFix} onClick={() => setShowFix((v) => !v)}
          className="h-11 rounded-md bg-accent px-4 text-[14px] font-bold text-accent-fg">How to fix</button>
        <button type="button" disabled={!demoAvailable} onClick={() => onDemo?.()}
          title={demoAvailable ? undefined : "Demo mode arrives with the demo clips"}
          className="h-11 rounded-md border border-border bg-surface px-4 text-[14px] font-semibold text-text disabled:opacity-50">Use a demo clip</button>
      </div>
      {showFix && (
        <ol className="m-0 flex list-none flex-col gap-2 rounded-md bg-surface-2 p-4 text-left text-[14px] leading-[1.4]">
          <li className="text-small font-semibold text-muted">On the camera phone, on the Claude Pet camera page:</li>
          {FIX_STEPS.map((s, i) => (
            <li key={s} className="flex gap-3">
              <span className="flex h-[22px] w-[22px] shrink-0 items-center justify-center rounded-full bg-surface text-[12px] font-bold">{i + 1}</span>
              {s}
            </li>
          ))}
        </ol>
      )}
    </section>
  );
}

export default function SystemNotice({ connected, everConnected, reconnectAttempt, status, lastFrameTs, onDemo }: Props) {
  if (!connected && everConnected) {
    return (
      <Banner>
        <span className="font-bold text-text">Reconnecting to Claude Pet</span>
        <span>attempt {reconnectAttempt}</span>
        {lastFrameTs && <span className="font-mono">last frame {hhmmss(lastFrameTs)}</span>}
      </Banner>
    );
  }
  const phone = phoneNotice(status);
  if (phone === "blocked") return <CameraBlocked status={status} onDemo={onDemo} />;
  if (status?.pipeline_status?.state === "stalled" || phone === "dropped") {
    return (
      <Banner>
        <span className="font-bold text-text">Reconnecting to camera</span>
        {lastFrameTs && <span className="font-mono">last frame {hhmmss(lastFrameTs)}</span>}
        <span>The camera phone dropped off Wi‑Fi. Keep it on and near the router.</span>
      </Banner>
    );
  }
  if (status?.llm?.enabled && !status.llm.online) {
    return (
      <Banner>
        <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" aria-hidden="true"><circle cx="12" cy="12" r="9" /><path d="M12 8h.01M11 12h1v4h1" /></svg>
        <span>AI is unreachable, so readings come from rules and may be less nuanced. Retrying automatically.</span>
      </Banner>
    );
  }
  return null;
}
