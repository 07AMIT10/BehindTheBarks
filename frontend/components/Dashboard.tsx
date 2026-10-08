"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import type { ClipMeta } from "@/lib/demo";
import { phoneNotice } from "@/lib/phone";
import { serverNow } from "@/lib/store";
import { spanAt } from "@/lib/timeline";
import { useBackend } from "@/lib/useBackend";
import { useNow } from "@/lib/useNow";
import AuthModal from "./AuthModal";
import ClipList from "./ClipList";
import DemoBanner from "./DemoBanner";
import DemoPlayer from "./DemoPlayer";
import EmotionCard from "./EmotionCard";
import Header from "./Header";
import NotificationsBell from "./NotificationsBell";
import SignalsPanel from "./SignalsPanel";
import StatusBar from "./StatusBar";
import SystemNotice from "./SystemNotice";
import ThemeToggle from "./ThemeToggle";
import Timeline from "./Timeline";
import ToastStack from "./ToastStack";
import TreatButton from "./TreatButton";
import VideoPanel from "./VideoPanel";
import PairingModal from "./PairingModal";

export default function Dashboard() {
  const backend = useBackend(); // backend.readAll / backend.http are used by Tasks 6 and 8
  const {
    state,
    treat,
    setMode,
    token,
    authRequired,
    authenticated,
    verifyPin,
    logout,
    privacyMode,
    setPrivacy,
  } = backend;
  const nowMs = useNow(1000);
  const nowSec = serverNow(state, nowMs);
  const projector = useMemo(() => new URLSearchParams(window.location.search).get("size") === "projector", []);
  const profile = state.status?.profile ?? { dog_name: "your dog", location: "Kitchen", zone_label: "feeding area" };
  const notice = phoneNotice(state.status);
  // A mic-only phone stalls the browser source on purpose; the camera-blocked card explains it instead.
  const stalled = state.status?.pipeline_status?.state === "stalled" && notice !== "blocked";
  const paused = (!state.connected && state.everConnected) || stalled || notice === "dropped";
  const lastSeenEmotion = useMemo(() => [...state.spans].reverse().find((s) => s.emotion !== "unknown")?.emotion ?? null, [state.spans]);

  const [treatFlash, setTreatFlash] = useState(false);
  const [selectedSpan, setSelectedSpan] = useState<number | null>(null);
  const [demoClip, setDemoClip] = useState<ClipMeta | null>(null);
  const [isPairingOpen, setIsPairingOpen] = useState(false);
  const mode = state.status?.mode ?? "live";
  const demo = mode === "demo";
  const viewMoment = useCallback((ts: number) => {
    const i = spanAt(state.spans, ts);
    if (i >= 0) setSelectedSpan(i);
    document.getElementById("timeline")?.scrollIntoView({ behavior: "smooth", block: "center" });
  }, [state.spans]);
  const flashTimer = useRef<ReturnType<typeof setTimeout> | undefined>(undefined);
  const onTreat = useCallback(() => {
    setTreatFlash(true);
    clearTimeout(flashTimer.current);
    flashTimer.current = setTimeout(() => setTreatFlash(false), 1500);
    void treat();
  }, [treat]);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.metaKey || e.ctrlKey || e.altKey || e.repeat) return;
      const tag = (e.target as HTMLElement | null)?.tagName;
      if (tag === "INPUT" || tag === "TEXTAREA" || tag === "SELECT") return;
      const k = e.key.toLowerCase();
      if (k === "d") { void setMode(demo ? "live" : "demo"); return; }
      if (k === "t" && !demo) onTreat();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onTreat, demo, setMode]);

  return (
    <div className={projector ? "projector" : undefined}>
      <div className="mx-auto flex w-full max-w-[1920px] flex-col gap-3 px-3 pb-28 pt-2 sm:gap-4 sm:px-4 lg:gap-5 lg:p-8">
        <Header dogName={profile.dog_name} location={profile.location}>
          <button
            type="button"
            onClick={() => setIsPairingOpen(true)}
            title="Pair Phone Station"
            className="flex h-9 items-center gap-1.5 px-3 rounded-lg border border-accent/40 bg-accent/10 text-accent hover:bg-accent/20 text-xs font-semibold transition-colors"
          >
            <span>📱</span>
            <span className="hidden sm:inline">
              {state.status?.phone?.connected ? "Station Paired" : "Pair Phone"}
            </span>
          </button>
          {token && (
            <button
              type="button"
              onClick={logout}
              title="Lock Dashboard / Sign out"
              className="flex h-9 w-9 items-center justify-center rounded-lg border border-border text-muted hover:text-white transition-colors"
            >
              <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
                <rect x="3" y="11" width="18" height="11" rx="2" ry="2" />
                <path d="M7 11V7a5 5 0 0 1 10 0v4" />
              </svg>
            </button>
          )}
          <ThemeToggle />
          <NotificationsBell notifications={state.notifications} dogName={profile.dog_name} onOpen={backend.readAll} />
        </Header>
        <StatusBar connected={state.connected} everConnected={state.everConnected} reconnectAttempt={state.reconnectAttempt}
          status={state.status} onMode={(m) => void setMode(m)} />
        <SystemNotice connected={state.connected} everConnected={state.everConnected} reconnectAttempt={state.reconnectAttempt}
          status={state.status} lastFrameTs={state.frame?.ts ?? null} onDemo={() => void setMode("demo")} />
        {demo && <DemoBanner onGoLive={() => void setMode("live")} />}
        {demo ? (
          <>
            <ClipList http={backend.http} activeId={demoClip?.id ?? null} onPick={setDemoClip} />
            {demoClip ? (
              <DemoPlayer http={backend.http} meta={demoClip} dogName={profile.dog_name} />
            ) : (
              <div className="text-small text-muted">Pick a clip to replay it through the full pipeline.</div>
            )}
          </>
        ) : (
          <>
          <div className="grid gap-3 sm:gap-4 [grid-template-areas:'video'_'card'_'signals'] lg:grid-cols-[minmax(0,3fr)_minmax(0,2fr)] lg:grid-rows-[auto_auto_1fr] lg:gap-6 lg:[grid-template-areas:'video_card'_'video_treat'_'video_signals']">
            <div className="min-h-[260px] sm:min-h-[340px] lg:min-h-[480px] [grid-area:video]">
              <VideoPanel
                http={backend.http}
                frame={state.frame}
                nowSec={nowSec}
                location={profile.location}
                paused={paused}
                lastDogTs={state.lastDogTs}
                privacyMode={privacyMode}
                onTogglePrivacy={() => void setPrivacy()}
                token={token}
              />
            </div>
            <div className="[grid-area:card]">
              <EmotionCard current={state.current} currentSince={state.currentSince} lastEmotionAt={state.lastEmotionAt}
                nowMs={nowMs} nowSec={nowSec} paused={paused} projector={projector} dogName={profile.dog_name}
                lastDogTs={state.lastDogTs} lastSeenEmotion={lastSeenEmotion} />
            </div>
            <div className="hidden [grid-area:treat] lg:block">
              <TreatButton onTreat={onTreat} flash={treatFlash} />
            </div>
            <div className="[grid-area:signals]">
              <SignalsPanel history={state.history} defaultOpen={!projector} />
            </div>
          </div>
          <div id="timeline">
            <Timeline spans={state.spans} audio={state.audio} treats={state.treats} notifications={state.notifications}
              nowSec={nowSec} selected={selectedSpan} onSelect={setSelectedSpan} />
          </div>
          </>
        )}
      </div>
      <AuthModal isOpen={authRequired && !authenticated} onVerify={verifyPin} />
      <PairingModal
        isOpen={isPairingOpen}
        onClose={() => setIsPairingOpen(false)}
        token={token}
        phoneConnected={state.status?.phone?.connected}
        deviceName={state.status?.phone?.device}
      />
      {!demo && (
        <div className="fixed inset-x-0 bottom-0 z-20 border-t border-border bg-bg/90 p-3 backdrop-blur lg:hidden"
          style={{ paddingBottom: "max(12px, env(safe-area-inset-bottom))" }}>
          <TreatButton onTreat={onTreat} flash={treatFlash} />
        </div>
      )}
      <ToastStack toasts={state.toasts} onDismiss={backend.dismissToast} onView={viewMoment} />
    </div>
  );
}
