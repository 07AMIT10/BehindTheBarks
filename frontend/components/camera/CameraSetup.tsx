"use client";

import { type Facing, ZONE_SETUP } from "@/lib/ingest";
import { CameraIcon, CameraModeHeader, LiveVideo, ZoneGuide } from "./bits";

type Props = {
  dogName: string;
  facing: Facing;
  stream: MediaStream | null;
  requesting: boolean;
  error: string | null;
  backendHost: string;
  reachable: boolean | null;
  onAllow: () => void;
  onPick: (f: Facing) => void;
  onStart: () => void;
};

const CAMS: { id: Facing; label: string; sub: string }[] = [
  { id: "back", label: "Back", sub: "Recommended" },
  { id: "front", label: "Front", sub: "Selfie" },
];

function StepNumber({ n }: { n: number }) {
  return (
    <span className="flex h-6 w-6 shrink-0 items-center justify-center rounded-full bg-accent-soft text-[13px] font-bold text-accent-ink">{n}</span>
  );
}

export default function CameraSetup(p: Props) {
  const allowed = p.stream !== null;
  return (
    <main className="mx-auto flex min-h-dvh w-full max-w-[480px] flex-col gap-5 bg-bg px-5 pb-7 pt-[52px] text-text">
      <CameraModeHeader />
      <div className="flex flex-col gap-2">
        <h1 className="m-0 text-[26px] font-bold leading-[1.2] tracking-[-0.015em]">Use this phone as {p.dogName}’s camera</h1>
        <p className="m-0 text-[15px] leading-[1.45] text-muted">Mount it near the food bowl and keep it plugged in. Your dashboard shows the feed.</p>
      </div>

      <ol className="m-0 flex list-none flex-col gap-3 p-0">
        <li className="flex flex-col gap-3 rounded-lg border border-border bg-surface p-4">
          <div className="flex items-start gap-3">
            <StepNumber n={1} />
            <div className="flex flex-col gap-1">
              <div className="text-[16px] font-semibold">Allow camera and microphone</div>
              <div className="text-[13px] leading-[1.45] text-muted">Used to read {p.dogName}’s posture, face and sounds. Your browser will ask once.</div>
            </div>
          </div>
          <button type="button" onClick={p.onAllow} disabled={allowed || p.requesting}
            className="flex h-11 items-center justify-center gap-2 rounded-md border border-accent bg-transparent text-[15px] font-semibold text-accent-ink disabled:opacity-60">
            <CameraIcon size={18} />
            {allowed ? "Access allowed" : p.requesting ? "Waiting for your browser…" : "Allow access"}
          </button>
          {p.error && <div role="alert" className="text-[13px] leading-[1.45]" style={{ color: "var(--aggressive-fg)" }}>{p.error}</div>}
        </li>

        <li className="flex flex-col gap-3 rounded-lg border border-border bg-surface p-4">
          <div className="flex items-center gap-3">
            <StepNumber n={2} />
            <div className="text-[16px] font-semibold">Choose camera</div>
          </div>
          <div role="radiogroup" aria-label="Camera" className="grid grid-cols-2 gap-1.5 rounded-[12px] bg-surface-2 p-1">
            {CAMS.map((c) => {
              const on = c.id === p.facing;
              return (
                <button key={c.id} type="button" role="radio" aria-checked={on} onClick={() => p.onPick(c.id)}
                  className={`flex h-11 flex-col items-center justify-center rounded-[9px] text-[14px] font-semibold ${on ? "bg-[#2A3035] text-text" : "bg-transparent text-muted"}`}>
                  {c.label}
                  <span className="text-[11px] font-medium opacity-75">{c.sub}</span>
                </button>
              );
            })}
          </div>
        </li>

        <li className="flex flex-col gap-3 rounded-lg border border-border bg-surface p-4">
          <div className="flex items-center gap-3">
            <StepNumber n={3} />
            <div className="text-[16px] font-semibold">Point at the food bowl</div>
          </div>
          <div className="relative h-[150px] overflow-hidden rounded-md bg-[#1B1D1F]">
            <LiveVideo stream={p.stream} mirror={p.facing === "front"} className="absolute inset-0 h-full w-full object-cover" />
            <ZoneGuide zone={ZONE_SETUP} radius={12} />
            <div className="absolute text-[11px] font-bold text-[#BFF2F4]" style={{ left: ZONE_SETUP.left, top: ZONE_SETUP.top, transform: "translateY(-120%)" }}>
              Feeding zone
            </div>
          </div>
          <div className="text-[13px] leading-[1.45] text-muted">Fit the bowl and about a metre around it inside the dashed zone. Waist height works best.</div>
        </li>
      </ol>

      <div className="grow" />
      <button type="button" onClick={p.onStart} disabled={!allowed}
        className="h-14 rounded-lg bg-accent text-[17px] font-bold text-accent-fg disabled:opacity-50">
        Start streaming
      </button>
      <div className="text-center text-[12px] text-muted">
        {p.reachable === false ? "Can’t reach the dashboard at " : "Paired with dashboard · "}
        <span className="font-mono text-text">{p.backendHost}</span>
      </div>
    </main>
  );
}
