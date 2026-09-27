"use client";

import { useEffect, useRef } from "react";
import { type MeterBar, type Quality, type ZoneGuide as Zone, meterBars } from "@/lib/ingest";

/** A muted, inline, autoplaying <video> showing a MediaStream (iOS needs muted + playsInline). */
export function LiveVideo({ stream, mirror, className, videoRef }: {
  stream: MediaStream | null;
  mirror: boolean;
  className: string;
  videoRef?: React.RefObject<HTMLVideoElement | null>;
}) {
  const own = useRef<HTMLVideoElement | null>(null);
  const ref = videoRef ?? own;
  useEffect(() => {
    const v = ref.current;
    if (!v) return;
    v.srcObject = stream;
    if (stream) void v.play().catch(() => undefined);
  }, [stream, ref]);
  return (
    <video ref={ref} autoPlay muted playsInline className={className}
      style={mirror ? { transform: "scaleX(-1)" } : undefined} />
  );
}

/** The dashed feeding-zone rectangle, positioned in % of its parent. */
export function ZoneGuide({ zone, radius, className = "" }: { zone: Zone; radius: number; className?: string }) {
  return (
    <div aria-hidden="true" className={`pointer-events-none absolute border-2 border-dashed border-[#7FE3E8] ${className}`}
      style={{ ...zone, borderRadius: radius }} />
  );
}

export function CameraIcon({ size, off = false }: { size: number; off?: boolean }) {
  return (
    <svg width={size} height={size} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
      <path d="M3 8a2 2 0 0 1 2-2h2l1.5-2h7L17 6h2a2 2 0 0 1 2 2v9a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2z" />
      <path d="M15 12.5a3 3 0 1 1-6 0a3 3 0 0 1 6 0z" />
      {off && <path d="M3 3l18 18" />}
    </svg>
  );
}

export function Logo() {
  return (
    <svg width="24" height="24" viewBox="0 0 32 32" fill="none" aria-hidden="true">
      <rect x="1" y="1" width="30" height="30" rx="9" stroke="var(--accent)" strokeWidth="2" />
      <path d="M7 17h4l2.5-6 4 11 2.5-5H25" stroke="var(--accent)" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" />
    </svg>
  );
}

/** "Camera mode" header row shared by setup (05) and blocked (08). */
export function CameraModeHeader() {
  return (
    <div className="flex items-center gap-2.5">
      <Logo />
      <div className="text-[14px] font-semibold text-muted">Camera mode</div>
    </div>
  );
}

export function QualityBars({ quality, width, height }: { quality: Quality; width: number; height: number }) {
  const rects = [
    { x: 0, y: 10, h: 4 }, { x: 4.3, y: 7, h: 7 }, { x: 8.6, y: 4, h: 10 }, { x: 13, y: 0, h: 14 },
  ];
  return (
    <svg width={width} height={height} viewBox="0 0 16 14" aria-hidden="true">
      {rects.map((r, i) => (
        <rect key={i} x={r.x} y={r.y} width="3" height={r.h} rx="1" fill={i < quality.bars ? "#7FE3E8" : "#4A4F55"} />
      ))}
    </svg>
  );
}

export function MicMeter({ level, count }: { level: number; count: number }) {
  const bars: MeterBar[] = meterBars(level, count);
  return (
    <div className="flex h-4 items-center gap-[3px]">
      {bars.map((b, i) => (
        <span key={i} className="w-[3px] rounded-[2px]" style={{ height: b.h, background: b.on ? "#7FE3E8" : "#4A4F55" }} />
      ))}
    </div>
  );
}

export function MicIcon() {
  return (
    <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="#D6D8DB" strokeWidth="1.9" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
      <rect x="9" y="3" width="6" height="11" rx="3" />
      <path d="M5.5 11a6.5 6.5 0 0 0 13 0M12 17.5V21" />
    </svg>
  );
}

export function SunIcon() {
  return (
    <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.9" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
      <path d="M12 3v2M12 19v2M4.2 4.2l1.4 1.4M18.4 18.4l1.4 1.4M3 12h2M19 12h2M4.2 19.8l1.4-1.4M18.4 5.6l1.4-1.4" />
      <path d="M16 12a4 4 0 1 1-8 0a4 4 0 0 1 8 0z" />
    </svg>
  );
}
