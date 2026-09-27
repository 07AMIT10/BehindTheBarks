"use client";

import { Component, Suspense, use, useCallback, useEffect, useMemo, useRef, useState, type ReactNode } from "react";
import { buildDemoView, fetchClip, type ClipMeta, type LoadedClip } from "@/lib/demo";
import { fitContain, drawOverlay } from "@/lib/overlay";
import { useNow } from "@/lib/useNow";
import EmotionCard from "./EmotionCard";
import SignalsPanel from "./SignalsPanel";
import Timeline from "./Timeline";
import ToastStack from "./ToastStack";

type Props = { http: string; meta: ClipMeta; dogName: string };

const clipCache = new Map<string, Promise<LoadedClip>>();

function loadClip(http: string, meta: ClipMeta): Promise<LoadedClip> {
  const key = `${http}|${meta.id}`;
  const hit = clipCache.get(key);
  if (hit) return hit;
  const p = fetchClip(http, meta);
  clipCache.set(key, p);
  p.catch(() => clipCache.delete(key));
  return p;
}

class ClipErrorBoundary extends Component<{ children: ReactNode }, { error: Error | null }> {
  state = { error: null as Error | null };
  static getDerivedStateFromError(error: Error) {
    return { error };
  }
  render() {
    if (this.state.error) {
      return <div role="alert" className="rounded-xl border border-border bg-surface p-6 text-small">Couldn&apos;t load this clip: {this.state.error.message}</div>;
    }
    return this.props.children;
  }
}

function DemoPlayerInner({ http, meta, dogName }: Props) {
  const clip = use(loadClip(http, meta));
  const nowMs = useNow(1000);
  const [t, setT] = useState(0);
  const [dismissed, setDismissed] = useState<Set<number>>(new Set());
  const [selected, setSelected] = useState<number | null>(null);
  const videoRef = useRef<HTMLVideoElement>(null);
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const boxRef = useRef<HTMLElement>(null);
  const [box, setBox] = useState({ w: 0, h: 0 });

  const view = useMemo(() => buildDemoView(clip, t, dismissed, dogName), [clip, t, dismissed, dogName]);

  useEffect(() => {
    const v = videoRef.current;
    if (!v) return;
    const onTime = () => setT(v.currentTime);
    v.addEventListener("timeupdate", onTime);
    v.addEventListener("seeked", onTime);
    return () => { v.removeEventListener("timeupdate", onTime); v.removeEventListener("seeked", onTime); };
  }, []);

  useEffect(() => {
    const el = boxRef.current;
    if (!el) return;
    const ro = new ResizeObserver(([entry]) => setBox({ w: entry.contentRect.width, h: entry.contentRect.height }));
    ro.observe(el);
    return () => ro.disconnect();
  }, []);

  useEffect(() => {
    const canvas = canvasRef.current, v = videoRef.current;
    if (!canvas || !v || box.w === 0) return;
    const dpr = window.devicePixelRatio || 1;
    canvas.width = Math.round(box.w * dpr);
    canvas.height = Math.round(box.h * dpr);
    const ctx = canvas.getContext("2d");
    if (ctx) drawOverlay(ctx, view.frame, fitContain(v.videoWidth || 640, v.videoHeight || 480, box.w, box.h), { box: true, skeleton: true, face: true }, dpr);
  }, [view, box]);

  const onView = useCallback((ts: number) => {
    const v = videoRef.current;
    if (v) { v.currentTime = Math.min(Math.max(0, ts), v.duration || ts); v.play().catch(() => undefined); }
  }, []);

  return (
    <div className="flex flex-col gap-4 lg:gap-6">
      <div className="grid gap-4 [grid-template-areas:'card'_'video'_'signals'] lg:grid-cols-[minmax(0,3fr)_minmax(0,2fr)] lg:grid-rows-[auto_auto_1fr] lg:gap-6 lg:[grid-template-areas:'video_card'_'video_treat'_'video_signals']">
        <div className="min-h-[216px] [grid-area:video] lg:min-h-[480px]">
          <section ref={boxRef} aria-label={`Demo clip: ${meta.name}`} className="relative h-full min-h-[216px] overflow-hidden rounded-xl bg-[#1B1D1F]">
            <video ref={videoRef} src={`${http}/demo/clips/${meta.id}/video`} controls playsInline preload="auto"
              className="absolute inset-0 h-full w-full object-contain" />
            <canvas ref={canvasRef} className="pointer-events-none absolute inset-0 h-full w-full" />
            <div className="absolute left-4 top-4 flex h-[30px] items-center gap-2 rounded-lg px-3 text-[12px] font-bold tracking-[0.06em] text-white" style={{ background: "var(--overlay-scrim)" }}>
              <span className="h-2 w-2 rounded-full" style={{ background: "var(--live)" }} />DEMO
            </div>
          </section>
        </div>
        <div className="[grid-area:card]">
          <EmotionCard current={view.emotion} currentSince={view.currentSince} lastEmotionAt={nowMs}
            nowMs={nowMs} nowSec={t} paused={false} projector={false} dogName={dogName}
            lastDogTs={null} lastSeenEmotion={null} />
        </div>
        <div className="[grid-area:signals]">
          <SignalsPanel history={view.history} defaultOpen />
        </div>
      </div>
      <Timeline spans={view.spans} audio={view.audio} treats={view.treats} notifications={view.notifications}
        nowSec={t} selected={selected} onSelect={setSelected} />
      <ToastStack toasts={view.toasts}
        onDismiss={(id) => {
          const m = /^demo-(\d+)$/.exec(id);
          if (m) setDismissed((d) => new Set(d).add(Number(m[1])));
        }}
        onView={onView} />
    </div>
  );
}

export default function DemoPlayer(props: Props) {
  return (
    <ClipErrorBoundary>
      <Suspense fallback={<div className="rounded-xl border border-border bg-surface p-6 text-small text-muted">Loading clip…</div>}>
        <DemoPlayerInner {...props} key={props.meta.id} />
      </Suspense>
    </ClipErrorBoundary>
  );
}
