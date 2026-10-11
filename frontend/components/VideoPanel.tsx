"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import type { FrameEvent } from "@/lib/contracts";
import { durationLabel, hhmmss } from "@/lib/format";
import { PAUSED_NOTICE, type StationState } from "@/lib/phone";
import { drawOverlay, fitContain, type Layers } from "@/lib/overlay";
import { StreamWatchdog } from "@/lib/watchdog";
import { useListen } from "@/lib/useListen";
import type { ModeRequestResult, ViewMode } from "@/lib/types";
import { ModeSheet } from "./ModeSheet";
import { ListenButton, ListenStatus } from "./ListenButton";

type Props = {
  http: string;
  frame: FrameEvent | null;
  nowSec: number;
  location: string;
  paused: boolean;
  idle?: boolean;
  blackoutPaused?: boolean;  // a deliberate Blackout (stationState "paused"): neutral card, no stream
  stream?: boolean;  // shouldStream(stationState): false for idle/paused/dropped, so no <img> and no /video request
  dropped?: boolean;  // the phone is attached but disconnected: "Reconnecting to camera", no stream
  onPair?: () => void;
  lastDogTs: number | null;
  privacyMode?: boolean;
  onTogglePrivacy?: () => void;
  token?: string | null;
  maxIdleSec?: number;
  effectiveMode?: ViewMode;
  stationCap?: ViewMode;
  householdMode?: ViewMode;
  modeCeiling?: ViewMode;
  stationState?: StationState;  // phone.ts stationState(): hides Listen when idle/paused/dropped
  signedIn?: boolean;  // false (household sign-out) stops listening and hides the control
  isOwner?: boolean;
  onSetMode?: (mode: ViewMode) => Promise<{ status: "applied" | "pending"; mode: ViewMode } | undefined>;
  onPeek?: () => Promise<boolean>;
  modeRequestResult?: ModeRequestResult | null;
  station?: string;
};

const chip = "flex h-11 md:h-8 items-center rounded-lg px-3 text-[12px]";
const scrim = { background: "var(--overlay-scrim)" };

export default function VideoPanel({
  http,
  frame,
  nowSec,
  location,
  paused,
  idle = false,
  blackoutPaused = false,
  stream = true,
  dropped = false,
  onPair,
  lastDogTs,
  privacyMode = false,
  onTogglePrivacy,
  token,
  maxIdleSec = 180,
  effectiveMode,
  stationCap = "full",
  householdMode = "full",
  modeCeiling = "full",
  stationState,
  signedIn = true,
  isOwner = true,
  onSetMode,
  onPeek,
  modeRequestResult = null,
  station,
}: Props) {
  const boxRef = useRef<HTMLDivElement>(null);
  const imgRef = useRef<HTMLImageElement>(null);
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const [layers, setLayers] = useState<Layers>({ box: true, skeleton: true, face: true });
  const [modeSheetOpen, setModeSheetOpen] = useState(false);
  const [retryKey, setRetryKey] = useState(0);
  const [videoOk, setVideoOk] = useState(false);
  const [size, setSize] = useState({ w: 0, h: 0 });
  const [watchdogPaused, setWatchdogPaused] = useState(false);
  const [resumeKey, setResumeKey] = useState(0);
  const watchdogRef = useRef<StreamWatchdog | null>(null);
  const listenStation: StationState =
    stationState ?? (idle ? "idle" : blackoutPaused ? "paused" : dropped ? "dropped" : "live");
  const listen = useListen({
    effectiveMode: effectiveMode ?? "blackout",
    viewerCeiling: modeCeiling,
    stationState: listenStation,
    signedIn,
    token,
    station,
    maxIdleSec,
  });

  useEffect(() => {
    const isMuted = idle || paused || privacyMode || effectiveMode === "blackout" || effectiveMode === "privacy";
    if (isMuted) {
      watchdogRef.current?.stop();
      return;
    }

    const wd = new StreamWatchdog(
      {
        onPause: () => setWatchdogPaused(true),
        onResume: () => setWatchdogPaused(false),
      },
      { maxIdleSec }
    );
    watchdogRef.current = wd;
    wd.start();

    const onActivity = () => wd.recordActivity();
    const onVisibility = () => wd.handleVisibilityChange();

    window.addEventListener("mousemove", onActivity, { passive: true });
    window.addEventListener("mousedown", onActivity, { passive: true });
    window.addEventListener("keydown", onActivity, { passive: true });
    window.addEventListener("touchstart", onActivity, { passive: true });
    window.addEventListener("scroll", onActivity, { passive: true });
    document.addEventListener("visibilitychange", onVisibility);

    return () => {
      wd.stop();
      window.removeEventListener("mousemove", onActivity);
      window.removeEventListener("mousedown", onActivity);
      window.removeEventListener("keydown", onActivity);
      window.removeEventListener("touchstart", onActivity);
      window.removeEventListener("scroll", onActivity);
      document.removeEventListener("visibilitychange", onVisibility);
    };
  }, [idle, paused, privacyMode, effectiveMode, maxIdleSec]);

  const handleResumeWatchdog = useCallback(() => {
    setWatchdogPaused(false);
    setResumeKey((k) => k + 1);
    watchdogRef.current?.resume();
  }, []);

  useEffect(() => {
    const box = boxRef.current;
    if (!box) return;
    const ro = new ResizeObserver(([entry]) => setSize({ w: entry.contentRect.width, h: entry.contentRect.height }));
    ro.observe(box);
    return () => ro.disconnect();
  }, []);

  useEffect(() => {
    const canvas = canvasRef.current;
    const img = imgRef.current;
    if (!canvas || !img || size.w === 0) return;
    const dpr = window.devicePixelRatio || 1;
    const cw = Math.round(size.w * dpr), ch = Math.round(size.h * dpr);
    if (canvas.width !== cw || canvas.height !== ch) {
      canvas.width = cw;
      canvas.height = ch;
    }
    const ctx = canvas.getContext("2d");
    if (!ctx) return;
    drawOverlay(ctx, paused || !videoOk ? null : frame, fitContain(img.naturalWidth, img.naturalHeight, size.w, size.h), layers, dpr);
  }, [frame, layers, paused, size, videoOk]);

  const onError = () => {
    setVideoOk(false);
    setTimeout(() => setRetryKey((k) => k + 1), 2000);
  };

  const [torchOn, setTorchOn] = useState(false);
  const [flipping, setFlipping] = useState(false);
  const [snapshotFlash, setSnapshotFlash] = useState(false);
  // Phones get one "controls" button opening a bottom sheet instead of the inline control bars.
  const [controlsOpen, setControlsOpen] = useState(false);

  const getAuthHeaders = useCallback((): Record<string, string> => {
    const h: Record<string, string> = { "Content-Type": "application/json" };
    if (token) h["Authorization"] = `Bearer ${token}`;
    return h;
  }, [token]);

  useEffect(() => {
    // Sync initial torch state from server
    const tokenParam = token ? `?token=${encodeURIComponent(token)}` : "";
    fetch(`${http}/torch${tokenParam}`, { headers: getAuthHeaders() })
      .then((r) => r.json())
      .then((d) => {
        if (typeof d.enabled === "boolean") setTorchOn(d.enabled);
      })
      .catch(() => {});
  }, [http, token, getAuthHeaders]);

  const toggleTorch = async () => {
    try {
      const tokenParam = token ? `?token=${encodeURIComponent(token)}` : "";
      const res = await fetch(`${http}/torch${tokenParam}`, {
        method: "POST",
        headers: getAuthHeaders(),
        body: JSON.stringify({ enabled: !torchOn }),
      });
      const data = await res.json();
      if (data.ok) setTorchOn(data.enabled);
    } catch (e) {
      console.warn("Torch failed", e);
    }
  };

  const flipCamera = async () => {
    setFlipping(true);
    try {
      const tokenParam = token ? `?token=${encodeURIComponent(token)}` : "";
      await fetch(`${http}/camera/flip${tokenParam}`, { method: "POST", headers: getAuthHeaders() });
      setTorchOn(false);
    } catch (e) {
      console.warn("Camera flip failed", e);
    } finally {
      setTimeout(() => setFlipping(false), 800);
    }
  };

  const [rotation, setRotation] = useState<0 | 90 | 180 | 270>(0);
  const isSideways = rotation === 90 || rotation === 270;
  const rotateVideo = () => setRotation((r) => ((r + 90) % 360) as 0 | 90 | 180 | 270);

  const takeSnapshot = () => {
    const img = imgRef.current;
    if (!img) return;
    setSnapshotFlash(true);
    setTimeout(() => setSnapshotFlash(false), 250);

    const w = img.naturalWidth || 640;
    const h = img.naturalHeight || 480;
    const exportCanvas = document.createElement("canvas");
    exportCanvas.width = isSideways ? h : w;
    exportCanvas.height = isSideways ? w : h;
    const ctx = exportCanvas.getContext("2d");
    if (!ctx) return;

    try {
      if (rotation !== 0) {
        ctx.translate(exportCanvas.width / 2, exportCanvas.height / 2);
        ctx.rotate((rotation * Math.PI) / 180);
        ctx.translate(-w / 2, -h / 2);
      }
      ctx.drawImage(img, 0, 0, w, h);
      if (frame) {
        drawOverlay(ctx, frame, { scale: 1, dx: 0, dy: 0 }, layers, 1);
      }
      exportCanvas.toBlob(
        (blob) => {
          if (!blob) return;
          const url = URL.createObjectURL(blob);
          const link = document.createElement("a");
          const d = new Date();
          const pad = (n: number) => String(n).padStart(2, "0");
          const dateStr = `${d.getFullYear()}${pad(d.getMonth() + 1)}${pad(d.getDate())}-${pad(d.getHours())}${pad(d.getMinutes())}${pad(d.getSeconds())}`;
          link.download = `wagwatch-${dateStr}.jpg`;
          link.href = url;
          link.click();
          URL.revokeObjectURL(url);
        },
        "image/jpeg",
        0.95
      );
    } catch (err) {
      console.warn("Snapshot capture warning", err);
    }
  };

  const kp = frame ? Object.values(frame.body_keypoints).filter(Boolean).length : 0;
  const facePts = frame?.face_landmarks?.length ?? 0;
  const noDog = frame !== null && !frame.dog_detected;
  const toggle = (k: keyof Layers) => setLayers((l) => ({ ...l, [k]: !l[k] }));
  // When the backend restarts, Chrome ends the MJPEG stream without an error event, so the <img> would
  // freeze on its last frame. Keying the stream on `paused` opens a fresh stream once we're live again.
  const tokenParam = token ? `&token=${encodeURIComponent(token)}` : "";
  const stationParam = station ? `&station=${encodeURIComponent(station)}` : "";
  const streamId = `${retryKey}${paused ? "p" : ""}${idle ? "i" : ""}${watchdogPaused ? "w" : ""}${resumeKey}`;
  const videoSrc = `${http}/video?k=${streamId}${stationParam}${tokenParam}`;

  // Monitoring is stopped on purpose (or has never started): no /video request at all, and a plain
  // card instead of a spinner — "Reconnecting…" is only for a phone that dropped mid-session.
  // The idle card keeps the same min-height rules as the live panel.
  if (blackoutPaused) {
    return (
      <section aria-label="Live video of the feeding area"
        className="relative h-full min-h-0 overflow-hidden rounded-xl bg-[#1B1D1F] sm:min-h-[320px] lg:min-h-[480px]">
        <div className="absolute inset-0 flex flex-col items-center justify-center gap-2 px-4 py-3 text-center text-[#D6D8DB] sm:gap-3 sm:p-6" data-testid="blackout-paused">
          <div className="hidden h-14 w-14 items-center justify-center rounded-2xl bg-white/5 text-[#9CA3AF] ring-1 ring-white/10 sm:flex">
            <svg width="28" height="28" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.7" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
              <path d="M2 8.5A2.5 2.5 0 0 1 4.5 6h2l1.6-2h7.8L17.5 6h2A2.5 2.5 0 0 1 22 8.5v7A2.5 2.5 0 0 1 19.5 18h-15A2.5 2.5 0 0 1 2 15.5z" />
              <path d="M3 3l18 18" />
            </svg>
          </div>
          <div className="text-heading font-semibold text-white">Paused</div>
          <p className="max-w-sm text-small text-[#9CA3AF]">{PAUSED_NOTICE}</p>
        </div>
      </section>
    );
  }

  if (idle) {
    return (
      <section aria-label="Live video of the feeding area"
        className="relative h-full min-h-0 overflow-hidden rounded-xl bg-[#1B1D1F] sm:min-h-[320px] lg:min-h-[480px]">
        <div className="absolute inset-0 flex flex-col items-center justify-center gap-2 px-4 py-3 text-center text-[#D6D8DB] sm:gap-3 sm:p-6">
          <div className="hidden h-14 w-14 items-center justify-center rounded-2xl bg-white/5 text-[#9CA3AF] ring-1 ring-white/10 sm:flex">
            <svg width="28" height="28" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.7" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
              <path d="M2 8.5A2.5 2.5 0 0 1 4.5 6h2l1.6-2h7.8L17.5 6h2A2.5 2.5 0 0 1 22 8.5v7A2.5 2.5 0 0 1 19.5 18h-15A2.5 2.5 0 0 1 2 15.5z" />
              <path d="M3 3l18 18" />
            </svg>
          </div>
          <div className="text-heading font-semibold text-white">Monitoring stopped</div>
          <p className="max-w-sm text-small text-[#9CA3AF]">
            Install the WagWatch app on a spare phone, open it, and scan the pairing QR from this page.
          </p>
          {onPair && (
            <button type="button" onClick={onPair}
              className="mt-1 flex h-11 shrink-0 items-center gap-2 rounded-lg bg-accent px-4 text-[14px] font-bold text-accent-fg transition-colors hover:bg-accent/90 active:scale-95">
              <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true"><rect x="3" y="11" width="18" height="11" rx="2" ry="2" /><path d="M7 11V7a5 5 0 0 1 10 0v4" /></svg>
              Pair a phone
            </button>
          )}
        </div>
      </section>
    );
  }

  return (
    <section aria-label="Live video of the feeding area" ref={boxRef}
      className="relative h-full min-h-0 overflow-hidden rounded-xl bg-[#1B1D1F] sm:min-h-[320px] lg:min-h-[480px]">
      <div
        className="absolute inset-0 flex items-center justify-center transition-transform duration-300"
        style={{
          transform: `rotate(${rotation}deg)${
            isSideways && size.w > 0 && size.h > 0
              ? ` scale(${Math.min(size.w / size.h, size.h / size.w)})`
              : ""
          }`,
        }}
      >
        {!watchdogPaused && stream && (
          /* eslint-disable-next-line @next/next/no-img-element -- MJPEG stream, next/image can't handle it */
          <img ref={imgRef} key={streamId} src={videoSrc} alt=""
            crossOrigin="anonymous"
            onLoad={() => setVideoOk(true)} onError={onError}
            className="h-full w-full object-contain" />
        )}
        <canvas ref={canvasRef} className="pointer-events-none absolute inset-0 h-full w-full" />
      </div>

      {snapshotFlash && (
        <div className="pointer-events-none absolute inset-0 z-20 bg-white/40 transition-opacity duration-300" />
      )}

      {effectiveMode === "limited" && !watchdogPaused && !idle && (
        <div className="absolute top-3 left-1/2 -translate-x-1/2 z-20 flex items-center gap-2 rounded-full border border-amber-500/30 bg-neutral-900/90 px-3.5 py-1 text-xs text-amber-300 backdrop-blur shadow-lg">
          <span>🖼️ Limited (Blurred stills)</span>
          {onPeek && (
            <button
              type="button"
              onClick={onPeek}
              className="rounded-full bg-amber-500/20 px-2 py-0.5 font-medium text-amber-200 hover:bg-amber-500/30 transition-colors active:scale-95"
            >
              Peek 15s
            </button>
          )}
        </div>
      )}

      {(effectiveMode === "blackout" || (privacyMode && !effectiveMode)) && (
        <div className="absolute inset-x-0 top-0 bottom-14 z-10 flex flex-col items-center justify-center gap-2 px-4 py-3 text-center text-[#D6D8DB] sm:inset-0 sm:gap-3 sm:p-6" style={{ background: "rgba(18, 20, 26, 0.9)", backdropFilter: "blur(12px)" }}>
          <div className="hidden h-14 w-14 items-center justify-center rounded-2xl bg-red-500/10 text-red-400 ring-1 ring-red-500/30 sm:flex">
            <svg width="28" height="28" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true"><path d="M12 22s8-4 8-10V5l-8-3-8 3v7c0 6 8 10 8 10z"/></svg>
          </div>
          <div className="text-lg font-bold text-white">Blackout Active</div>
          <p className="max-w-xs text-xs text-[#9CA3AF]">
            Live camera preview & microphone are muted at the station. Tap below to adjust.
          </p>
          <button type="button" onClick={() => (onSetMode ? setModeSheetOpen(true) : onTogglePrivacy?.())}
            className="mt-1 flex min-h-11 items-center rounded-lg bg-red-600/80 px-4 py-1.5 text-xs font-semibold text-white shadow-lg hover:bg-red-500 transition-colors active:scale-95 md:min-h-8">
            {onSetMode ? "Change Mode" : "Resume Monitoring"}
          </button>
        </div>
      )}

      {effectiveMode === "privacy" && (
        <div className="absolute inset-x-0 top-0 bottom-14 z-10 flex flex-col items-center justify-center gap-2 px-4 py-3 text-center text-[#D6D8DB] sm:inset-0 sm:gap-3 sm:p-6" style={{ background: "rgba(18, 20, 26, 0.9)", backdropFilter: "blur(12px)" }}>
          <div className="hidden h-14 w-14 items-center justify-center rounded-2xl bg-amber-500/10 text-amber-400 ring-1 ring-amber-500/30 sm:flex">
            <svg width="28" height="28" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true"><path d="M17.94 17.94A10.07 10.07 0 0 1 12 20c-7 0-11-8-11-8a18.45 18.45 0 0 1 5.06-5.94M9.9 4.24A9.12 9.12 0 0 1 12 4c7 0 11 8 11 8a18.5 18.5 0 0 1-2.16 3.19m-6.72-1.07a3 3 0 1 1-4.24-4.24M1 1l22 22"/></svg>
          </div>
          <div className="text-lg font-bold text-white">Privacy Mode Active</div>
          <p className="max-w-xs text-xs text-[#9CA3AF]">
            Telemetry, barks, and behavior analysis active. Optical room video is disabled.
          </p>
          <button type="button" onClick={() => (onSetMode ? setModeSheetOpen(true) : onTogglePrivacy?.())}
            className="mt-1 flex min-h-11 items-center rounded-lg bg-amber-600/80 px-4 py-1.5 text-xs font-semibold text-white md:min-h-8 shadow-lg hover:bg-amber-500 transition-colors active:scale-95">
            Change Mode
          </button>
        </div>
      )}

      {watchdogPaused && !privacyMode && !idle && (
        <div
          onClick={handleResumeWatchdog}
          className="absolute inset-0 z-30 flex cursor-pointer flex-col items-center justify-center gap-3 p-6 text-center text-[#D6D8DB]"
          style={{ background: "rgba(18, 20, 26, 0.95)", backdropFilter: "blur(12px)" }}
        >
          <div className="flex h-14 w-14 items-center justify-center rounded-2xl bg-[#7FE3E8]/10 text-[#7FE3E8] ring-1 ring-[#7FE3E8]/30">
            <svg width="28" height="28" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
              <rect x="6" y="4" width="4" height="16" />
              <rect x="14" y="4" width="4" height="16" />
            </svg>
          </div>
          <div className="text-lg font-bold text-white">Stream paused to protect household privacy</div>
          <p className="max-w-xs text-xs text-[#9CA3AF]">
            Inactivity watchdog paused the live feed. Tap anywhere to resume.
          </p>
          <button
            type="button"
            onClick={handleResumeWatchdog}
            className="mt-1 rounded-lg bg-[#7FE3E8] px-4 py-1.5 text-xs font-bold text-[#12141A] shadow-lg hover:bg-[#7FE3E8]/90 transition-colors active:scale-95"
          >
            Tap to resume
          </button>
        </div>
      )}

      {(paused || !videoOk) && !privacyMode && !watchdogPaused && (
        <div className="absolute inset-0 flex flex-col items-center justify-center gap-2 px-4 py-3 text-center text-[#D6D8DB] sm:gap-3 sm:p-6" style={scrim}>
          <svg width="28" height="28" viewBox="0 0 24 24" fill="none" stroke="#7FE3E8" strokeWidth="2" strokeLinecap="round"
            style={{ animation: "spin 1.2s linear infinite" }} aria-hidden="true"><path d="M12 3a9 9 0 1 0 9 9" /></svg>
          <div className="text-heading font-semibold text-white">{dropped ? "Reconnecting to camera…" : paused ? "Reconnecting…" : "Waiting for video…"}</div>
          {paused && <div className="max-w-sm text-small">The camera phone dropped off Wi‑Fi. Keep it on and near the router.</div>}
        </div>
      )}

      {/* Top status bar: Live indicator + Rotate button + Dog status */}
      <div className="absolute inset-x-3 top-3 flex items-center justify-between gap-2">
        <div className="flex flex-wrap items-center gap-1.5 sm:gap-2">
          <div className={`${chip} gap-1.5 sm:gap-2 font-bold tracking-[0.06em] text-white text-[12px] sm:text-[12px]`} style={scrim}>
            <span className="h-2 w-2 rounded-full" style={{ background: "var(--live)" }} />LIVE
            <span className="font-mono font-normal tracking-normal text-[#D6D8DB]">{hhmmss(nowSec)}</span>
          </div>
          <div className={`${chip} text-[#D6D8DB] text-[12px] sm:text-[12px] hidden sm:flex`} style={scrim}>{location} · bowl cam</div>
          {noDog && (
            <div className={`${chip} gap-1 text-white text-[12px] sm:text-[12px]`} style={scrim}>
              No dog in view{lastDogTs && <span className="font-mono text-[#A0A5AD]">· {durationLabel(nowSec - lastDogTs)}</span>}
            </div>
          )}
        </div>

        <div className="hidden items-center gap-1.5 sm:flex">
          <button
            type="button"
            onClick={rotateVideo}
            title={`Rotate stream 90° clockwise (current: ${rotation}°)`}
            className="flex h-11 md:h-8 items-center gap-1.5 rounded-lg px-2.5 text-[12px] font-semibold text-[#D6D8DB] hover:text-white transition-all active:scale-95 border border-white/10"
            style={scrim}
          >
            <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
              <path d="M21.5 2v6h-6M21.34 15.57a10 10 0 1 1-.57-8.38l5.67-5.67" />
            </svg>
            <span>{rotation !== 0 ? `${rotation}°` : "Rotate"}</span>
          </button>
        </div>
      </div>

      {/* Bottom controls bar: responsive and mobile-adaptive */}
      <div className="absolute inset-x-2 bottom-2 z-20 flex flex-wrap items-center justify-between gap-1.5 sm:inset-x-4 sm:bottom-4 sm:gap-2">
        <div className="flex min-w-0 flex-wrap items-center gap-1.5">
          <div className={`${chip} hidden gap-2 text-[#D6D8DB] text-[12px] sm:text-[12px] sm:flex`} style={scrim}>
            <span>Pose {kp} kp</span>
            <span>Face {facePts} pts</span>
          </div>
          <ListenStatus control={listen} />
        </div>

        <div className="flex flex-wrap items-center gap-1.5">
          <div role="group" aria-label="Camera controls" className="hidden gap-0.5 rounded-md p-1 sm:flex" style={scrim}>
            <button type="button" onClick={takeSnapshot} title="Save instant photo"
              className="flex h-11 md:h-8 items-center gap-1 rounded-sm px-2 sm:px-2.5 text-[12px] sm:text-[12px] font-semibold text-[#D6D8DB] hover:text-white transition-colors active:scale-95">
              <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true"><path d="M23 19a2 2 0 0 1-2 2H3a2 2 0 0 1-2-2V8a2 2 0 0 1 2-2h4l2-3h6l2 3h4a2 2 0 0 1 2 2z"/><circle cx="12" cy="13" r="4"/></svg>
              <span className="hidden sm:inline">Photo</span>
            </button>
            <button type="button" aria-pressed={torchOn} onClick={toggleTorch} title={torchOn ? "Turn off room light" : "Turn on room light"}
              className="flex h-11 md:h-8 items-center gap-1 rounded-sm px-2 sm:px-2.5 text-[12px] sm:text-[12px] font-semibold transition-all active:scale-95"
              style={torchOn ? { background: "rgba(255, 235, 59, 0.22)", color: "#FFF59D", boxShadow: "0 0 8px rgba(255, 235, 59, 0.35)" } : { color: "#A0A5AD" }}>
              <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true"><path d="M9 18h6"/><path d="M10 22h4"/><path d="M12 2v1"/><path d="M12 7a5 5 0 0 0-5 5c0 2 1.5 3.5 2.5 4.5.5.5.5 1.5.5 1.5h4s0-1 .5-1.5c1-1 2.5-2.5 2.5-4.5a5 5 0 0 0-5-5z"/></svg>
              <span>{torchOn ? "Light ON" : "Light"}</span>
            </button>
            <button type="button" onClick={flipCamera} disabled={flipping} title="Switch between front and back camera"
              className="flex h-11 md:h-8 items-center gap-1 rounded-sm px-2 sm:px-2.5 text-[12px] sm:text-[12px] font-semibold text-[#D6D8DB] hover:text-white transition-all active:scale-95 disabled:opacity-50">
              <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true"><path d="M21 12a9 9 0 0 0-9-9 9.75 9.0 0 0 0-6.74 2.74L3 8"/><path d="M3 3v5h5"/><path d="M3 12a9 9 0 0 0 9 9 9.75 9.0 0 0 0 6.74-2.74L21 16"/><path d="M16 21h5v-5"/></svg>
              <span className="hidden sm:inline">Flip</span>
            </button>
            {onSetMode ? (
              <button
                type="button"
                onClick={() => setModeSheetOpen(true)}
                title={`Viewing mode: ${effectiveMode ?? "full"}. Click to change.`}
                className="flex h-11 md:h-8 items-center gap-1.5 rounded-sm px-2 sm:px-2.5 text-[12px] sm:text-[12px] font-semibold transition-all active:scale-95"
                style={
                  effectiveMode === "blackout"
                    ? { background: "rgba(239, 68, 68, 0.25)", color: "#FCA5A5", border: "1px solid rgba(239, 68, 68, 0.5)" }
                    : effectiveMode === "privacy"
                    ? { background: "rgba(245, 158, 11, 0.25)", color: "#FCD34D", border: "1px solid rgba(245, 158, 11, 0.5)" }
                    : effectiveMode === "limited"
                    ? { background: "rgba(245, 158, 11, 0.15)", color: "#FDE68A" }
                    : { color: "#A0A5AD" }
                }
              >
                <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true"><path d="M12 22s8-4 8-10V5l-8-3-8 3v7c0 6 8 10 8 10z"/></svg>
                <span className="capitalize">{effectiveMode ?? "Mode"}</span>
              </button>
            ) : onTogglePrivacy ? (
              <button type="button" aria-pressed={privacyMode} onClick={onTogglePrivacy}
                title={privacyMode ? "Disable Privacy Mode (Resume monitoring)" : "Enable Privacy Mode (Mute camera & mic)"}
                className="flex h-11 md:h-8 items-center gap-1 rounded-sm px-2 sm:px-2.5 text-[12px] sm:text-[12px] font-semibold transition-all active:scale-95"
                style={privacyMode ? { background: "rgba(239, 68, 68, 0.25)", color: "#FCA5A5", border: "1px solid rgba(239, 68, 68, 0.5)" } : { color: "#A0A5AD" }}>
                <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true"><path d="M12 22s8-4 8-10V5l-8-3-8 3v7c0 6 8 10 8 10z"/></svg>
                <span>{privacyMode ? "Privacy ON" : "Privacy"}</span>
              </button>
            ) : null}
            <ListenButton control={listen} variant="bar" />
          </div>

          <div role="group" aria-label="Overlay layers" className="hidden gap-0.5 rounded-md p-1 sm:flex" style={scrim}>
            {(["box", "skeleton", "face"] as const).map((k) => (
              <button key={k} type="button" aria-pressed={layers[k]} onClick={() => toggle(k)}
                className="h-11 md:h-8 rounded-sm px-2 sm:px-2.5 text-[12px] font-semibold transition-colors"
                style={layers[k] ? { background: "rgba(127,227,232,.18)", color: "#BFF2F4" } : { color: "#A0A5AD" }}>
                {k === "box" ? "Box" : k === "skeleton" ? "Skel" : "Face"}
              </button>
            ))}
          </div>

          {/* Phone widths: photo and a controls button, both >= 44 px; the rest lives in the sheet. */}
          <div role="group" aria-label="Camera controls" className="flex gap-1.5 sm:hidden" style={scrim}>
            <button type="button" onClick={takeSnapshot} title="Save instant photo" aria-label="Save instant photo"
              className="flex h-11 w-11 items-center justify-center rounded-lg text-[#D6D8DB] active:scale-95">
              <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true"><path d="M23 19a2 2 0 0 1-2 2H3a2 2 0 0 1-2-2V8a2 2 0 0 1 2-2h4l2-3h6l2 3h4a2 2 0 0 1 2 2z"/><circle cx="12" cy="13" r="4"/></svg>
            </button>
            <button type="button" onClick={() => setControlsOpen(true)} aria-expanded={controlsOpen}
              className="flex h-11 items-center gap-1.5 rounded-lg px-3 text-[13px] font-semibold text-[#D6D8DB] active:scale-95">
              <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true"><path d="M4 21v-7M4 10V3M12 21v-9M12 8V3M20 21v-5M20 12V3M1 14h6M9 8h6M17 16h6"/></svg>
              Controls
            </button>
          </div>
        </div>
      </div>

      {/* Bottom sheet (phones only): the secondary camera actions and overlay layers, >= 44 px each. */}
      {controlsOpen && (
        <div className="fixed inset-0 z-30 sm:hidden">
          <button type="button" aria-label="Close controls" onClick={() => setControlsOpen(false)}
            className="absolute inset-0 h-full w-full cursor-default bg-black/50" />
          <div className="absolute inset-x-3 bottom-24 rounded-2xl border border-white/10 bg-[#121417]/95 p-4 backdrop-blur"
            style={{ paddingBottom: "max(16px, env(safe-area-inset-bottom))" }}>
            <div className="mb-3 flex items-center justify-between">
              <span className="text-[14px] font-bold text-white">Camera controls</span>
              <button type="button" onClick={() => setControlsOpen(false)} aria-label="Close controls"
                className="flex h-11 w-11 items-center justify-center rounded-lg text-[#D6D8DB] active:scale-95">
                <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true"><path d="M18 6L6 18M6 6l12 12"/></svg>
              </button>
            </div>
            <div className="grid grid-cols-2 gap-2">
              <button type="button" aria-pressed={torchOn} onClick={toggleTorch}
                className="flex h-11 items-center justify-center gap-2 rounded-xl px-3 text-[14px] font-semibold text-[#D6D8DB] ring-1 ring-white/10 active:scale-95"
                style={torchOn ? { background: "rgba(255, 235, 59, 0.22)", color: "#FFF59D" } : undefined}>
                <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true"><path d="M9 18h6"/><path d="M10 22h4"/><path d="M12 2v1"/><path d="M12 7a5 5 0 0 0-5 5c0 2 1.5 3.5 2.5 4.5.5.5.5 1.5.5 1.5h4s0-1 .5-1.5c1-1 2.5-2.5 2.5-4.5a5 5 0 0 0-5-5z"/></svg>
                {torchOn ? "Light ON" : "Light"}
              </button>
              <button type="button" onClick={flipCamera} disabled={flipping}
                className="flex h-11 items-center justify-center gap-2 rounded-xl px-3 text-[14px] font-semibold text-[#D6D8DB] ring-1 ring-white/10 active:scale-95 disabled:opacity-50">
                <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true"><path d="M21 12a9 9 0 0 0-9-9 9.75 9.0 0 0 0-6.74 2.74L3 8"/><path d="M3 3v5h5"/><path d="M3 12a9 9 0 0 0 9 9 9.75 9.0 0 0 0 6.74-2.74L21 16"/><path d="M16 21h5v-5"/></svg>
                Flip
              </button>
              <button type="button" onClick={rotateVideo}
                className="flex h-11 items-center justify-center gap-2 rounded-xl px-3 text-[14px] font-semibold text-[#D6D8DB] ring-1 ring-white/10 active:scale-95">
                <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true"><path d="M21.5 2v6h-6M21.34 15.57a10 10 0 1 1-.57-8.38l5.67-5.67"/></svg>
                Rotate{rotation !== 0 ? ` ${rotation}°` : ""}
              </button>
              {onSetMode ? (
                <button
                  type="button"
                  onClick={() => {
                    setControlsOpen(false);
                    setModeSheetOpen(true);
                  }}
                  className="flex h-11 items-center justify-center gap-2 rounded-xl px-3 text-[14px] font-semibold ring-1 active:scale-95"
                  style={
                    effectiveMode === "blackout"
                      ? { background: "rgba(239, 68, 68, 0.25)", color: "#FCA5A5", borderColor: "rgba(239, 68, 68, 0.5)" }
                      : effectiveMode === "privacy"
                      ? { background: "rgba(245, 158, 11, 0.25)", color: "#FCD34D", borderColor: "rgba(245, 158, 11, 0.5)" }
                      : { color: "#D6D8DB" }
                  }
                >
                  <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true"><path d="M12 22s8-4 8-10V5l-8-3-8 3v7c0 6 8 10 8 10z"/></svg>
                  <span className="capitalize">Mode: {effectiveMode ?? "full"}</span>
                </button>
              ) : onTogglePrivacy ? (
                <button type="button" aria-pressed={privacyMode} onClick={onTogglePrivacy}
                  className="flex h-11 items-center justify-center gap-2 rounded-xl px-3 text-[14px] font-semibold ring-1 active:scale-95"
                  style={privacyMode ? { background: "rgba(239, 68, 68, 0.25)", color: "#FCA5A5", borderColor: "rgba(239, 68, 68, 0.5)" } : { color: "#D6D8DB" }}>
                  <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true"><path d="M12 22s8-4 8-10V5l-8-3-8 3v7c0 6 8 10 8 10z"/></svg>
                  {privacyMode ? "Privacy ON" : "Privacy"}
                </button>
              ) : null}
            </div>
            <div className="mt-3">
              <ListenButton control={listen} variant="sheet" />
            </div>
            <div className="mt-3 flex gap-2">
              {(["box", "skeleton", "face"] as const).map((k) => (
                <button key={k} type="button" aria-pressed={layers[k]} onClick={() => toggle(k)}
                  className="h-11 flex-1 rounded-xl text-[14px] font-semibold ring-1 ring-white/10 active:scale-95"
                  style={layers[k] ? { background: "rgba(127,227,232,.18)", color: "#BFF2F4" } : { color: "#A0A5AD" }}>
                  {k === "box" ? "Box" : k === "skeleton" ? "Skel" : "Face"}
                </button>
              ))}
            </div>
          </div>
        </div>
      )}

      {onSetMode && (
        <ModeSheet
          isOpen={modeSheetOpen}
          onClose={() => setModeSheetOpen(false)}
          effectiveMode={effectiveMode ?? "full"}
          stationCap={stationCap}
          householdMode={householdMode}
          modeCeiling={modeCeiling}
          isOwner={isOwner}
          onSetMode={onSetMode}
          onPeek={onPeek}
          requestResult={modeRequestResult}
        />
      )}
    </section>
  );
}
