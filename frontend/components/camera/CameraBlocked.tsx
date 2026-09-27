"use client";

import { CameraIcon, CameraModeHeader } from "./bits";

type Props = { dogName: string; error: string | null; onRetry: () => void; onMicOnly: () => void };

const STEPS = [
  "Tap the site settings icon next to the address.",
  "Set Camera and Microphone to Allow.",
  "Come back and tap Try again.",
];

export default function CameraBlocked({ dogName, error, onRetry, onMicOnly }: Props) {
  return (
    <main className="mx-auto flex min-h-dvh w-full max-w-[480px] flex-col gap-6 bg-bg px-5 pb-7 pt-[52px] text-text">
      <CameraModeHeader />
      <div className="flex grow flex-col justify-center gap-5">
        <div className="flex h-[72px] w-[72px] items-center justify-center rounded-2xl border border-border bg-surface-2 text-text-soft">
          <CameraIcon size={34} off />
        </div>
        <div className="flex flex-col gap-2">
          <h1 className="m-0 text-[26px] font-bold leading-[1.2]">Camera access is blocked</h1>
          <p className="m-0 text-[15px] leading-normal text-muted">Claude Pet needs the camera to see {dogName}’s posture and face. Your browser blocked it for this site.</p>
        </div>
        <ol className="m-0 flex list-none flex-col gap-3 rounded-lg border border-border bg-surface p-4 text-[14px] leading-[1.4]">
          {STEPS.map((s, i) => (
            <li key={s} className="flex gap-3">
              <span className="flex h-[22px] w-[22px] shrink-0 items-center justify-center rounded-full bg-surface-2 text-[12px] font-bold">{i + 1}</span>
              {s}
            </li>
          ))}
        </ol>
        {error && <div role="alert" className="text-[13px] leading-[1.45]" style={{ color: "var(--aggressive-fg)" }}>{error}</div>}
      </div>
      <div className="flex flex-col gap-2.5">
        <button type="button" onClick={onRetry} className="h-14 rounded-lg bg-accent text-[17px] font-bold text-accent-fg">Try again</button>
        <button type="button" onClick={onMicOnly}
          className="flex min-h-14 flex-col items-center justify-center gap-0.5 rounded-lg border border-border bg-transparent p-2 text-[15px] font-semibold text-text">
          Continue with microphone only
          <span className="text-[12px] font-medium text-muted">Emotion from sounds only · lower confidence</span>
        </button>
      </div>
    </main>
  );
}
