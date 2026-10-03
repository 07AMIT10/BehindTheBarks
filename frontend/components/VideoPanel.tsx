"use client";

import { useEffect, useRef, useState } from "react";
import type { FrameEvent } from "@/lib/contracts";
import { durationLabel, hhmmss } from "@/lib/format";
import { drawOverlay, fitContain, type Layers } from "@/lib/overlay";

type Props = {
  http: string;
  frame: FrameEvent | null;
  nowSec: number;
  location: string;
  paused: boolean;
  lastDogTs: number | null;
};

const chip = "flex h-[30px] items-center rounded-lg px-3 text-[12px]";
const scrim = { background: "var(--overlay-scrim)" };

export default function VideoPanel({ http, frame, nowSec, location, paused, lastDogTs }: Props) {
  const boxRef = useRef<HTMLDivElement>(null);
  const imgRef = useRef<HTMLImageElement>(null);
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const [layers, setLayers] = useState<Layers>({ box: true, skeleton: true, face: true });
  const [retryKey, setRetryKey] = useState(0);
  const [videoOk, setVideoOk] = useState(false);
  const [size, setSize] = useState({ w: 0, h: 0 });

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

  useEffect(() => {
    // Sync initial torch state from server
    fetch(`${http}/torch`)
      .then((r) => r.json())
      .then((d) => {
        if (typeof d.enabled === "boolean") setTorchOn(d.enabled);
      })
      .catch(() => {});
  }, [http]);

  const toggleTorch = async () => {
    try {
      const res = await fetch(`${http}/torch`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
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
      await fetch(`${http}/camera/flip`, { method: "POST" });
      setTorchOn(false);
    } catch (e) {
      console.warn("Camera flip failed", e);
    } finally {
      setTimeout(() => setFlipping(false), 800);
    }
  };

  const takeSnapshot = () => {
    const img = imgRef.current;
    if (!img) return;
    setSnapshotFlash(true);
    setTimeout(() => setSnapshotFlash(false), 250);

    const w = img.naturalWidth || 640;
    const h = img.naturalHeight || 480;
    const exportCanvas = document.createElement("canvas");
    exportCanvas.width = w;
    exportCanvas.height = h;
    const ctx = exportCanvas.getContext("2d");
    if (!ctx) return;

    try {
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
          link.download = `bruno-${dateStr}.jpg`;
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
  const streamId = `${retryKey}${paused ? "p" : ""}`;

  return (
    <section aria-label="Live video of the feeding area" ref={boxRef}
      className="relative h-full min-h-[216px] overflow-hidden rounded-xl bg-[#1B1D1F]">
      {/* eslint-disable-next-line @next/next/no-img-element -- MJPEG stream, next/image can't handle it */}
      <img ref={imgRef} key={streamId} src={`${http}/video?k=${streamId}`} alt=""
        crossOrigin="anonymous"
        onLoad={() => setVideoOk(true)} onError={onError}
        className="absolute inset-0 h-full w-full object-contain" />
      <canvas ref={canvasRef} className="pointer-events-none absolute inset-0 h-full w-full" />

      {snapshotFlash && (
        <div className="pointer-events-none absolute inset-0 z-20 bg-white/40 transition-opacity duration-300" />
      )}

      {(paused || !videoOk) && (
        <div className="absolute inset-0 flex flex-col items-center justify-center gap-3 p-6 text-center text-[#D6D8DB]" style={scrim}>
          <svg width="28" height="28" viewBox="0 0 24 24" fill="none" stroke="#7FE3E8" strokeWidth="2" strokeLinecap="round"
            style={{ animation: "spin 1.2s linear infinite" }} aria-hidden="true"><path d="M12 3a9 9 0 1 0 9 9" /></svg>
          <div className="text-heading font-semibold text-white">{paused ? "Reconnecting…" : "Waiting for video…"}</div>
          {paused && <div className="max-w-sm text-small">The camera phone dropped off Wi‑Fi. Keep it on and near the router.</div>}
        </div>
      )}

      <div className="absolute left-4 top-4 flex flex-wrap gap-2">
        <div className={`${chip} gap-2 font-bold tracking-[0.06em] text-white`} style={scrim}>
          <span className="h-2 w-2 rounded-full" style={{ background: "var(--live)" }} />LIVE
          <span className="font-mono font-normal tracking-normal text-[#D6D8DB]">{hhmmss(nowSec)}</span>
        </div>
        <div className={`${chip} text-[#D6D8DB]`} style={scrim}>{location} · bowl cam</div>
        {noDog && (
          <div className={`${chip} gap-1 text-white`} style={scrim}>
            No dog in view{lastDogTs && <span className="font-mono text-[#A0A5AD]">· {durationLabel(nowSec - lastDogTs)}</span>}
          </div>
        )}
      </div>

      <div className="absolute inset-x-4 bottom-4 flex flex-wrap items-center gap-2">
        <div className={`${chip} gap-3 text-[#D6D8DB]`} style={scrim}><span>Pose {kp} kp</span><span>Face {facePts} pts</span></div>
        <div className="grow" />

        <div role="group" aria-label="Camera controls" className="flex gap-1 rounded-md p-1" style={scrim}>
          <button type="button" onClick={takeSnapshot} title="Save instant photo of Bruno"
            className="flex h-[30px] items-center gap-1.5 rounded-sm px-2.5 text-[12px] font-semibold text-[#D6D8DB] hover:text-white transition-colors active:scale-95">
            <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true"><path d="M23 19a2 2 0 0 1-2 2H3a2 2 0 0 1-2-2V8a2 2 0 0 1 2-2h4l2-3h6l2 3h4a2 2 0 0 1 2 2z"/><circle cx="12" cy="13" r="4"/></svg>
            Photo
          </button>
          <button type="button" aria-pressed={torchOn} onClick={toggleTorch} title={torchOn ? "Turn off room light" : "Turn on room light"}
            className="flex h-[30px] items-center gap-1.5 rounded-sm px-2.5 text-[12px] font-semibold transition-all active:scale-95"
            style={torchOn ? { background: "rgba(255, 235, 59, 0.22)", color: "#FFF59D", boxShadow: "0 0 8px rgba(255, 235, 59, 0.35)" } : { color: "#A0A5AD" }}>
            <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true"><path d="M9 18h6"/><path d="M10 22h4"/><path d="M12 2v1"/><path d="M12 7a5 5 0 0 0-5 5c0 2 1.5 3.5 2.5 4.5.5.5.5 1.5.5 1.5h4s0-1 .5-1.5c1-1 2.5-2.5 2.5-4.5a5 5 0 0 0-5-5z"/></svg>
            {torchOn ? "Light ON" : "Light"}
          </button>
          <button type="button" onClick={flipCamera} disabled={flipping} title="Switch between front and back camera"
            className="flex h-[30px] items-center gap-1.5 rounded-sm px-2.5 text-[12px] font-semibold text-[#D6D8DB] hover:text-white transition-all active:scale-95 disabled:opacity-50">
            <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" className={flipping ? "animate-spin" : ""} aria-hidden="true"><path d="M21 12a9 9 0 0 0-9-9 9.75 9.75 0 0 0-6.74 2.74L3 8"/><path d="M3 3v5h5"/><path d="M3 12a9 9 0 0 0 9 9 9.75 9.75 0 0 0 6.74-2.74L21 16"/><path d="M16 21h5v-5"/></svg>
            Flip
          </button>
        </div>

        <div role="group" aria-label="Overlay layers" className="flex gap-1 rounded-md p-1" style={scrim}>
          {(["box", "skeleton", "face"] as const).map((k) => (
            <button key={k} type="button" aria-pressed={layers[k]} onClick={() => toggle(k)}
              className="h-[30px] rounded-sm px-2.5 text-[12px] font-semibold"
              style={layers[k] ? { background: "rgba(127,227,232,.18)", color: "#BFF2F4" } : { color: "#A0A5AD" }}>
              {k === "box" ? "Box" : k === "skeleton" ? "Skeleton" : "Face"}
            </button>
          ))}
        </div>
      </div>
    </section>
  );
}
