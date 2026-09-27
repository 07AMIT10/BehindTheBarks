"use client";

import { useRef } from "react";
import {
  type Facing, METER_BARS_LANDSCAPE, METER_BARS_PORTRAIT, ZONE_LANDSCAPE, ZONE_PORTRAIT, elapsedLabel,
} from "@/lib/ingest";
import { type StreamStats, useIngestStream } from "@/lib/useIngestStream";
import { LiveVideo, MicIcon, MicMeter, QualityBars, SunIcon, ZoneGuide } from "./bits";

type Props = {
  stream: MediaStream;
  audioCtx: AudioContext | null;
  camera: boolean;
  facing: Facing;
  fps: number;
  wsBase: string;
  onStop: () => void;
};

const PILL = "flex items-center rounded-full bg-[rgba(15,17,19,.78)]";

function LiveDot({ live }: { live: boolean }) {
  return (
    <span className="h-2 w-2 rounded-full"
      style={live ? { background: "var(--live)", boxShadow: "0 0 0 4px rgba(255,90,78,.25)" } : { background: "#8F949B" }} />
  );
}

function streamLabel(s: StreamStats): string {
  if (s.status === "open") return "Streaming";
  if (s.status === "connecting") return "Connecting…";
  if (s.status === "retrying") return "Reconnecting…";
  return "Stopped";
}

export default function CameraStreaming({ stream, audioCtx, camera, facing, fps, wsBase, onStop }: Props) {
  const videoRef = useRef<HTMLVideoElement | null>(null);
  const s = useIngestStream({ stream, audioCtx, video: videoRef, camera, facing, fps, wsBase });
  const live = s.status === "open";
  const quality = `Connection ${s.quality.label.toLowerCase()}`;

  return (
    <main className="fixed inset-0 overflow-hidden bg-[#1B1D1F] font-sans text-white">
      {camera ? (
        <LiveVideo stream={stream} mirror={facing === "front"} videoRef={videoRef} className="absolute inset-0 h-full w-full object-contain" />
      ) : (
        <div className="absolute inset-0 flex items-center justify-center px-8 text-center text-[15px] text-[#D6D8DB]">
          Emotion from sounds only · lower confidence
        </div>
      )}
      {camera && <ZoneGuide zone={ZONE_PORTRAIT} radius={16} className="landscape:hidden" />}
      {camera && <ZoneGuide zone={ZONE_LANDSCAPE} radius={16} className="portrait:hidden" />}

      {/* Portrait (06) */}
      <div className="absolute inset-x-4 top-14 flex flex-col gap-2 landscape:hidden">
        <div className="flex items-center gap-2">
          <div className={`${PILL} h-9 gap-2 px-3 text-[13px] font-bold`}>
            <LiveDot live={live} />{streamLabel(s)}
            {live && <span className="font-mono font-normal text-[#D6D8DB]">{elapsedLabel(s.elapsedMs)}</span>}
          </div>
          <div className="grow" />
          <div className={`${PILL} h-9 gap-2 px-3 text-[12px] text-[#D6D8DB]`} aria-label={quality}>
            <QualityBars quality={s.quality} width={16} height={14} />{s.quality.label}
          </div>
          {camera && <div className={`${PILL} h-9 px-3 font-mono text-[12px] text-[#D6D8DB]`}>{s.sentFps} fps</div>}
        </div>
        <div className={`${PILL} h-9 gap-2.5 self-start px-3`} aria-label="Microphone level">
          <MicIcon /><MicMeter level={s.level} count={METER_BARS_PORTRAIT} />
        </div>
        {s.message && <div role="status" className={`${PILL} self-start rounded-[14px] px-3 py-2 text-[13px] text-[#D6D8DB]`}>{s.message}</div>}
      </div>
      <div className="absolute inset-x-4 bottom-8 flex flex-col items-center gap-3.5 landscape:hidden">
        <div className={`${PILL} h-[34px] gap-2 px-3.5 text-[13px] text-[#D6D8DB]`}><SunIcon />Keep this screen on · plugged in</div>
        <button type="button" onClick={onStop}
          className="flex h-16 w-full items-center justify-center gap-3 rounded-[18px] bg-[#F4F4F2] text-[18px] font-bold text-[#17181A]">
          <span className="h-4 w-4 rounded-[3px] bg-[#17181A]" />Stop streaming
        </button>
      </div>

      {/* Landscape (07) */}
      <div className="absolute left-5 top-5 flex flex-col items-start gap-2 portrait:hidden">
        <div className={`${PILL} h-[34px] gap-2 px-3 text-[13px] font-bold`}>
          <LiveDot live={live} />{streamLabel(s)}
          {live && <span className="font-mono font-normal text-[#D6D8DB]">{elapsedLabel(s.elapsedMs)}</span>}
        </div>
        <div className={`${PILL} h-[30px] gap-1.5 px-2.5 text-[12px] text-[#D6D8DB]`} aria-label={quality}>
          <QualityBars quality={s.quality} width={14} height={12} />{s.quality.label}
        </div>
        {camera && <div className={`${PILL} h-[30px] px-2.5 font-mono text-[12px] text-[#D6D8DB]`}>{s.sentFps} fps</div>}
        {s.message && <div role="status" className={`${PILL} max-w-[280px] rounded-[14px] px-3 py-2 text-[13px] text-[#D6D8DB]`}>{s.message}</div>}
      </div>
      <div className={`${PILL} absolute bottom-5 left-5 h-[34px] gap-2.5 px-3 portrait:hidden`} aria-label="Microphone level">
        <MicIcon /><MicMeter level={s.level} count={METER_BARS_LANDSCAPE} />
      </div>
      <div className={`${PILL} absolute bottom-5 left-1/2 h-[30px] -translate-x-1/2 px-3 text-[12px] text-[#D6D8DB] portrait:hidden`}>
        Keep this screen on · plugged in
      </div>
      <button type="button" onClick={onStop}
        className="absolute right-5 top-1/2 flex h-[88px] w-[88px] -translate-y-1/2 flex-col items-center justify-center gap-1.5 rounded-full bg-[#F4F4F2] text-[13px] font-bold text-[#17181A] portrait:hidden">
        <span className="h-[18px] w-[18px] rounded-[4px] bg-[#17181A]" />Stop
      </button>
    </main>
  );
}
