"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import type { ClipMeta } from "@/lib/demo";
import { phoneNotice, stationState } from "@/lib/phone";
import { welcomeVisible } from "@/lib/household";
import { serverNow } from "@/lib/store";
import { spanAt } from "@/lib/timeline";
import { useBackend } from "@/lib/useBackend";
import { useIsMobile } from "@/lib/useIsMobile";
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
import PairingModal, { type Pairing } from "./PairingModal";
import MembersModal from "./MembersModal";
import WelcomeScreen from "./WelcomeScreen";
import DeleteHouseholdModal from "./DeleteHouseholdModal";
import ProfileModal from "./ProfileModal";
import StationGrid from "./StationGrid";

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
    saveProfile,
    authStatus,
    authMode,
    me,
    createHousehold,
    recoverHousehold,
    logoutHousehold,
    deleteHousehold,
    createPairing,
    joinHousehold,
    fetchMembers,
    fetchSettings,
    saveSettings,
    revokeViewer,
    revokeStation,
  } = backend;
  const nowMs = useNow(1000);
  const nowSec = serverNow(state, nowMs);
  const isMobile = useIsMobile();
  const [timelineOpen, setTimelineOpen] = useState(false);
  const [showGrid, setShowGrid] = useState(false);
  const stations = state.status?.stations ?? [];
  const hasMultipleStations = stations.length > 1;
  const projector = useMemo(() => new URLSearchParams(window.location.search).get("size") === "projector", []);
  const profile = state.status?.profile ?? { dog_name: "Your dog", location: "", zone_label: "feeding area" };
  const notice = phoneNotice(state.status);
  // A mic-only phone stalls the browser source on purpose; the camera-blocked card explains it instead.
  const st = stationState(state.status);
  const idle = st === "idle"; // the phone stopped on purpose, or none has ever connected: not an error
  const stalled = st === "stalled" && notice !== "blocked";
  const paused = (!state.connected && state.everConnected) || stalled || st === "dropped";
  const lastSeenEmotion = useMemo(() => [...state.spans].reverse().find((s) => s.emotion !== "unknown")?.emotion ?? null, [state.spans]);

  const [treatFlash, setTreatFlash] = useState(false);
  const [selectedSpan, setSelectedSpan] = useState<number | null>(null);
  const [demoClip, setDemoClip] = useState<ClipMeta | null>(null);
  const [isPairingOpen, setIsPairingOpen] = useState(false);
  // A fresh key per open re-seeds the form with the live profile (no effect, no reset on broadcasts).
  const [profileOpen, setProfileOpen] = useState<number | null>(null);
  const [deleteOpen, setDeleteOpen] = useState(false);
  const [membersOpen, setMembersOpen] = useState(false);
  // The pairing code is fetched on click (never in an effect, so no cascading renders); a fresh key
  // per open re-seeds it, like the profile sheet (T1.8).
  const [pairOpenCount, setPairOpenCount] = useState(0);
  const [pairing, setPairing] = useState<Pairing | null>(null);
  const [pairKind, setPairKind] = useState<"station" | "viewer">("station");
  const [pairError, setPairError] = useState<string | null>(null);

  const mintPairing = useCallback((kind: "station" | "viewer", ceiling = "full") => {
    setPairKind(kind);
    setPairError(null);
    setPairing(null);
    void createPairing(kind, kind === "viewer" ? { mode_ceiling: ceiling, label: "viewer" } : {})
      .then(setPairing)
      .catch((err) => setPairError(err instanceof Error ? err.message : "could not create the code"));
  }, [createPairing]);
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

  // An invite link (/?join=<code>) adopts this browser as a viewer, then reloads into the dashboard.
  useEffect(() => {
    if (typeof window === "undefined") return;
    const code = new URLSearchParams(window.location.search).get("join");
    if (!code || authenticated) return;
    void joinHousehold(code, "viewer")
      .then(() => window.location.reload())
      .catch(() => undefined);
  }, [authenticated, joinHousehold]);

  if (welcomeVisible(authStatus, authenticated)) {
    return <WelcomeScreen onCreate={createHousehold} onRecover={recoverHousehold} />;
  }

  return (
    <div className={projector ? "projector" : undefined}>
      <div className="mx-auto flex w-full max-w-[1920px] flex-col gap-3 px-3 pb-28 pt-2 sm:gap-4 sm:px-4 lg:gap-5 lg:p-8">
        <Header dogName={profile.dog_name} location={profile.location}>
          <button
            type="button"
            onClick={() => { setPairOpenCount((n) => n + 1); setIsPairingOpen(true); mintPairing("station"); }}
            title="Pair Phone Station"
            className="flex h-9 items-center gap-1.5 px-3 rounded-lg border border-accent/40 bg-accent/10 text-accent hover:bg-accent/20 text-xs font-semibold transition-colors"
          >
            <span>📱</span>
            <span className="hidden sm:inline">
              {state.status?.phone?.connected ? "Station Paired" : "Pair Phone"}
            </span>
          </button>
          <button
            type="button"
            onClick={() => setProfileOpen((n) => (n ?? 0) + 1)}
            title="Edit pet profile"
            aria-label="Edit pet profile"
            className="flex h-9 w-9 items-center justify-center rounded-lg border border-border text-muted hover:text-white transition-colors"
          >
            <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true"><path d="M12 20h9"/><path d="M16.5 3.5a2.12 2.12 0 0 1 3 3L7 19l-4 1 1-4z"/></svg>
          </button>
          {authMode === "households" && authenticated && (
            <>
              <button
                type="button"
                onClick={() => setMembersOpen(true)}
                title="Household members"
                aria-label="Household members"
                className="flex h-9 w-9 items-center justify-center rounded-lg border border-border text-muted hover:text-white transition-colors"
              >
                <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true"><path d="M17 21v-2a4 4 0 0 0-4-4H5a4 4 0 0 0-4 4v2"/><circle cx="9" cy="7" r="4"/><path d="M23 21v-2a4 4 0 0 0-3-3.87"/><path d="M16 3.13a4 4 0 0 1 0 7.75"/></svg>
              </button>
              <button
                type="button"
                onClick={() => setDeleteOpen(true)}
                title="Delete household"
                aria-label="Delete household"
                className="flex h-9 w-9 items-center justify-center rounded-lg border border-red-500/40 text-red-300 hover:text-white transition-colors"
              >
                <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true"><path d="M3 6h18"/><path d="M19 6v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6"/><path d="M8 6V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2"/></svg>
              </button>
              <button
                type="button"
                onClick={() => void logoutHousehold()}
                title="Sign out"
                aria-label="Sign out"
                className="flex h-9 w-9 items-center justify-center rounded-lg border border-border text-muted hover:text-white transition-colors"
              >
                <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true"><path d="M9 21H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h4"/><path d="M16 17l5-5-5-5M21 12H9"/></svg>
              </button>
            </>
          )}
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
        ) : hasMultipleStations && showGrid ? (
          <div className="flex flex-col gap-4">
            <div className="flex items-center justify-between">
              <div className="flex items-center gap-2">
                <span className="text-body font-bold text-text">Household Cameras</span>
                <span className="rounded-full bg-surface-2 px-2 py-0.5 text-[11px] font-semibold text-muted">
                  {stations.length}
                </span>
              </div>
              <button
                type="button"
                onClick={() => setShowGrid(false)}
                className="flex items-center gap-1.5 rounded-lg border border-border bg-surface px-3 py-1.5 text-small font-semibold text-text hover:bg-surface-2 transition-colors"
              >
                Back to {state.stations[state.focusedStation]?.name || "Camera"}
              </button>
            </div>
            <StationGrid
              http={backend.http}
              token={token}
              stations={stations}
              stationViews={state.stations}
              focusedStation={state.focusedStation}
              onFocus={(id) => {
                backend.focusStation(id);
                setShowGrid(false);
              }}
            />
          </div>
        ) : (
          <>
          {hasMultipleStations && (
            <div className="mb-2 flex items-center justify-between">
              <div className="flex items-center gap-2">
                <span className="text-small font-semibold text-text">
                  Viewing: {state.stations[state.focusedStation]?.name || "Camera"}
                </span>
              </div>
              <button
                type="button"
                onClick={() => setShowGrid(true)}
                className="flex items-center gap-1.5 rounded-lg border border-border bg-surface px-3 py-1 text-small font-semibold text-text hover:bg-surface-2 transition-colors"
              >
                <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
                  <rect x="3" y="3" width="7" height="7" /><rect x="14" y="3" width="7" height="7" /><rect x="14" y="14" width="7" height="7" /><rect x="3" y="14" width="7" height="7" />
                </svg>
                Camera Grid ({stations.length})
              </button>
            </div>
          )}
          <div className="grid gap-3 sm:gap-4 [grid-template-areas:'video'_'card'_'signals'] lg:grid-cols-[minmax(0,3fr)_minmax(0,2fr)] lg:grid-rows-[auto_auto_1fr] lg:gap-6 lg:[grid-template-areas:'video_card'_'video_treat'_'video_signals']">
            <div className="aspect-video sm:aspect-auto sm:min-h-[340px] lg:min-h-[480px] [grid-area:video]">
              <VideoPanel
                http={backend.http}
                frame={state.frame}
                nowSec={nowSec}
                location={profile.location}
                paused={paused}
                idle={idle}
                onPair={() => setIsPairingOpen(true)}
                lastDogTs={state.lastDogTs}
                privacyMode={privacyMode}
                onTogglePrivacy={() => void setPrivacy()}
                token={token}
                maxIdleSec={state.status?.max_idle_s}
                effectiveMode={backend.effectiveMode}
                stationCap={backend.stationCap}
                householdMode={backend.householdMode}
                modeCeiling={backend.me?.mode_ceiling}
                isOwner={!backend.me || backend.me.role === "owner"}
                onSetMode={backend.setViewMode}
                onPeek={backend.peek}
                station={state.focusedStation !== "default" ? state.focusedStation : undefined}
              />
            </div>
            <div className="[grid-area:card]">
              <EmotionCard current={state.current} currentSince={state.currentSince} lastEmotionAt={state.lastEmotionAt}
                nowMs={nowMs} nowSec={nowSec} paused={paused} idle={idle} projector={projector} dogName={profile.dog_name}
                lastDogTs={state.lastDogTs} lastSeenEmotion={lastSeenEmotion} />
            </div>
            <div className="hidden [grid-area:treat] lg:block">
              <TreatButton onTreat={onTreat} flash={treatFlash} />
            </div>
            <div className="[grid-area:signals]">
              <SignalsPanel history={state.history} defaultOpen={!projector && !isMobile} />
            </div>
          </div>
          {/* Phones get a disclosure for the timeline; desktop shows it inline. */}
          <div id="timeline">
            <button type="button" aria-expanded={timelineOpen} onClick={() => setTimelineOpen((v) => !v)}
              className="mb-2 flex w-full items-center justify-between rounded-md border border-border bg-surface px-4 py-2.5 text-small font-semibold text-text lg:hidden">
              Timeline
              <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true"
                style={{ transform: timelineOpen ? "rotate(180deg)" : "none" }}>
                <path d="M6 9l6 6 6-6" />
              </svg>
            </button>
            <div className={timelineOpen ? "" : "hidden lg:block"}>
              <Timeline spans={state.spans} audio={state.audio} treats={state.treats} notifications={state.notifications}
                nowSec={nowSec} selected={selectedSpan} onSelect={setSelectedSpan} />
            </div>
          </div>
          </>
        )}
      </div>
      <AuthModal isOpen={authRequired && !authenticated} onVerify={verifyPin} />
      <DeleteHouseholdModal isOpen={deleteOpen} onClose={() => setDeleteOpen(false)} onDelete={deleteHousehold} />
      {profileOpen !== null && (
        <ProfileModal
          key={profileOpen}
          isOpen
          onClose={() => setProfileOpen(null)}
          profile={state.status?.profile}
          onSave={saveProfile}
        />
      )}
      {isPairingOpen && (
        <PairingModal
          key={pairOpenCount}
          isOpen
          kind={pairKind}
          pairing={pairing}
          error={pairError}
          onClose={() => setIsPairingOpen(false)}
          phoneConnected={state.status?.phone?.connected}
          deviceName={state.status?.phone?.device}
          onRegenerate={() => mintPairing(pairKind)}
          onSwitchKind={(k) => mintPairing(k)}
        />
      )}
      <MembersModal
        isOpen={membersOpen}
        onClose={() => setMembersOpen(false)}
        isOwner={me?.role === "owner"}
        fetchMembers={fetchMembers}
        revokeViewer={revokeViewer}
        revokeStation={revokeStation}
        fetchSettings={fetchSettings}
        saveSettings={saveSettings}
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
