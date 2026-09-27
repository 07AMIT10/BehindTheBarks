"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { phoneNotice } from "@/lib/phone";
import { serverNow } from "@/lib/store";
import { spanAt } from "@/lib/timeline";
import { useBackend } from "@/lib/useBackend";
import { useNow } from "@/lib/useNow";
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

export default function Dashboard() {
  const backend = useBackend(); // backend.readAll / backend.http are used by Tasks 6 and 8
  const { state, treat, setMode } = backend;
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
      if (e.key.toLowerCase() !== "t" || e.metaKey || e.ctrlKey || e.altKey || e.repeat) return;
      const tag = (e.target as HTMLElement | null)?.tagName;
      if (tag === "INPUT" || tag === "TEXTAREA" || tag === "SELECT") return;
      onTreat();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onTreat]);

  return (
    <div className={projector ? "projector" : undefined}>
      <div className="mx-auto flex w-full max-w-[1920px] flex-col gap-4 px-4 pb-28 pt-3 lg:gap-5 lg:p-8">
        <Header dogName={profile.dog_name} location={profile.location}>
          <ThemeToggle />
          <NotificationsBell notifications={state.notifications} dogName={profile.dog_name} onOpen={backend.readAll} />
        </Header>
        <StatusBar connected={state.connected} everConnected={state.everConnected} reconnectAttempt={state.reconnectAttempt}
          status={state.status} onMode={(m) => void setMode(m)} />
        <SystemNotice connected={state.connected} everConnected={state.everConnected} reconnectAttempt={state.reconnectAttempt}
          status={state.status} lastFrameTs={state.frame?.ts ?? null} onDemo={() => void setMode("demo")} />
        <div className="grid gap-4 [grid-template-areas:'card'_'video'_'signals'] lg:grid-cols-[minmax(0,3fr)_minmax(0,2fr)] lg:grid-rows-[auto_auto_1fr] lg:gap-6 lg:[grid-template-areas:'video_card'_'video_treat'_'video_signals']">
          <div className="min-h-[216px] [grid-area:video] lg:min-h-[480px]">
            <VideoPanel http={backend.http} frame={state.frame} nowSec={nowSec} location={profile.location}
              paused={paused} lastDogTs={state.lastDogTs} />
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
      </div>
      <div className="fixed inset-x-0 bottom-0 z-20 border-t border-border bg-bg/90 p-3 backdrop-blur lg:hidden"
        style={{ paddingBottom: "max(12px, env(safe-area-inset-bottom))" }}>
        <TreatButton onTreat={onTreat} flash={treatFlash} />
      </div>
      <ToastStack toasts={state.toasts} onDismiss={backend.dismissToast} onView={viewMoment} />
    </div>
  );
}
