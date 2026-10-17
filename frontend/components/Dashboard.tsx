"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import type { ClipMeta } from "@/lib/demo";
import { phoneNotice, shouldStream, stationState } from "@/lib/phone";
import { welcomeVisible } from "@/lib/household";
import { serverNow } from "@/lib/store";
import { spanAt } from "@/lib/timeline";
import { useBackend } from "@/lib/useBackend";
import { useIsMobile } from "@/lib/useIsMobile";
import { useNow } from "@/lib/useNow";
import { batteryDisplay, batteryLevelClass, isTelemetryStale } from "@/lib/battery";
import type { NotificationItem } from "@/lib/types";
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
import DoggyDiary from "./DoggyDiary";
import ToastStack from "./ToastStack";
import TreatButton from "./TreatButton";
import VideoPanel from "./VideoPanel";
import PairingModal, { type Pairing } from "./PairingModal";
import MembersModal from "./MembersModal";
import WelcomeScreen from "./WelcomeScreen";
import DeleteHouseholdModal from "./DeleteHouseholdModal";
import ProfileModal from "./ProfileModal";
import StationGrid from "./StationGrid";
import DebugDrawer from "./DebugDrawer";
import MomentModal, { type MomentDetail } from "./MomentModal";
import SoundboardDrawer from "./SoundboardDrawer";
import MobileMenuDrawer from "./MobileMenuDrawer";
import type { Toast } from "@/lib/toasts";

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
  const [debugOpen, setDebugOpen] = useState(false);
  const [soundboardOpen, setSoundboardOpen] = useState(false);
  const stations = useMemo(() => state.status?.stations ?? [], [state.status?.stations]);
  const hasMultipleStations = stations.length > 1;
  const projector = useMemo(() => new URLSearchParams(window.location.search).get("size") === "projector", []);
  const profile = state.status?.profile ?? { dog_name: "Your dog", location: "", zone_label: "feeding area" };
  const notice = phoneNotice(state.status);
  // A mic-only phone stalls the browser source on purpose; the camera-blocked card explains it instead.
  const st = stationState(state.status);
  const idle = st === "idle"; // the phone stopped on purpose, or none has ever connected: not an error
  const blackoutPaused = st === "paused"; // a deliberate Blackout: neutral card, no reconnecting
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
  const [mobileMenuOpen, setMobileMenuOpen] = useState(false);
  // The pairing code is fetched on click (never in an effect, so no cascading renders); a fresh key
  // per open re-seeds it, like the profile sheet (T1.8).
  const [pairOpenCount, setPairOpenCount] = useState(0);
  const [pairing, setPairing] = useState<Pairing | null>(null);
  const [pairKind, setPairKind] = useState<"station" | "viewer">("station");
  const [pairError, setPairError] = useState<string | null>(null);
  const [initialStationCount, setInitialStationCount] = useState<number | null>(null);

  const mintPairing = useCallback((kind: "station" | "viewer", ceiling = "full") => {
    setPairKind(kind);
    setPairError(null);
    setPairing(null);
    void createPairing(kind, kind === "viewer" ? { mode_ceiling: ceiling, label: "viewer" } : {})
      .then(setPairing)
      .catch((err) => setPairError(err instanceof Error ? err.message : "could not create the code"));
  }, [createPairing]);

  const openPairModal = useCallback((kind: "station" | "viewer" = "station") => {
    setPairOpenCount((n) => n + 1);
    setIsPairingOpen(true);
    setInitialStationCount(stations.length);
    mintPairing(kind);
  }, [stations.length, mintPairing]);

  const pairedStation = useMemo(() => {
    if (!isPairingOpen) return null;
    if (state.status?.paired_station) {
      return state.status.paired_station;
    }
    if (initialStationCount !== null && stations.length > initialStationCount) {
      return stations[stations.length - 1];
    }
    return null;
  }, [isPairingOpen, state.status?.paired_station, stations, initialStationCount]);
  const mode = state.status?.mode ?? "live";
  const demo = mode === "demo";
  const [activeMoment, setActiveMoment] = useState<MomentDetail | null>(null);

  const viewMoment = useCallback((ts: number, toast?: Toast) => {
    const i = spanAt(state.spans, ts);
    if (i >= 0) {
      setSelectedSpan(i);
      const sp = state.spans[i];
      setActiveMoment({
        emotion: sp.emotion,
        ts: sp.start,
        reason: sp.reason,
        confidence: sp.confidence,
        source: sp.source,
        snapshot: sp.snapshot ?? toast?.snapshot ?? null,
        duration_s: sp.end > sp.start ? sp.end - sp.start : undefined,
      });
    } else if (toast?.emotion) {
      setActiveMoment({
        emotion: toast.emotion,
        ts: toast.ts,
        reason: toast.body || toast.title,
        snapshot: toast.snapshot ?? null,
      });
    }
    document.getElementById("timeline")?.scrollIntoView({ behavior: "smooth", block: "center" });
  }, [state.spans]);

  const onSelectNotification = useCallback((n: NotificationItem) => {
    setActiveMoment({
      emotion: n.state.emotion,
      ts: n.state.ts,
      reason: n.state.reason,
      confidence: n.state.confidence,
      source: n.state.source,
      snapshot: n.state.snapshot ?? null,
    });
  }, []);

  const flashTimer = useRef<ReturnType<typeof setTimeout> | undefined>(undefined);
  const onTreat = useCallback(() => {
    setTreatFlash(true);
    clearTimeout(flashTimer.current);
    flashTimer.current = setTimeout(() => setTreatFlash(false), 1500);
    void treat();
  }, [treat]);

  const [activeCueId, setActiveCueId] = useState<string | null>(null);
  const cueTimer = useRef<ReturnType<typeof setTimeout> | undefined>(undefined);
  const triggerQuickCue = useCallback(
    async (sound: string, light?: string, _label?: string) => {
      void _label;
      const cueKey = sound + (light ? `_${light}` : "");
      setActiveCueId(cueKey);
      clearTimeout(cueTimer.current);
      cueTimer.current = setTimeout(() => setActiveCueId(null), 1200);
      try {
        const stationId = state.focusedStation !== "default" ? state.focusedStation : undefined;
        const tokenParam = token ? `?token=${encodeURIComponent(token)}` : "";
        const headers: Record<string, string> = { "Content-Type": "application/json" };
        if (token) headers["Authorization"] = `Bearer ${token}`;
        const controller = new AbortController();
        const timer = setTimeout(() => controller.abort(), 4000);
        try {
          await fetch(`${backend.http}/cue${tokenParam}`, {
            method: "POST",
            headers,
            credentials: "include",
            body: JSON.stringify({ sound, light, station: stationId }),
            signal: controller.signal,
          });
        } finally {
          clearTimeout(timer);
        }
      } catch (e) {
        console.warn("Failed to trigger quick cue", e);
      }
    },
    [backend.http, token, state.focusedStation]
  );

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

  const focusedStationSummary = stations.find((st) => st.id === state.focusedStation);
  const phoneObj = focusedStationSummary?.phone ?? state.status?.phone;
  const activeBatteryPct = focusedStationSummary?.phone?.battery_pct ?? focusedStationSummary?.battery ?? state.status?.phone?.battery_pct ?? null;
  const activeIsCharging = Boolean(phoneObj?.charging);
  const telemetryTs = phoneObj?.latest_telemetry?.server_ts ?? phoneObj?.latest_telemetry?.ts;
  const activeLastAge = phoneObj?.last_frame_age_s ?? phoneObj?.last_event_age_s;
  const isConnected = Boolean(phoneObj?.connected);
  const activeIsStale = !isConnected || (telemetryTs != null ? isTelemetryStale(telemetryTs) : (activeLastAge != null ? isTelemetryStale(activeLastAge) : false));
  const activeBClass = batteryLevelClass(activeBatteryPct, activeIsStale);
  const activeBText = batteryDisplay(activeBatteryPct, activeIsStale);

  return (
    <div className={projector ? "projector" : undefined}>
      <div className="mx-auto flex w-full max-w-[1920px] flex-col gap-3 px-3 pb-28 pt-2 sm:gap-4 sm:px-4 lg:gap-5 lg:p-8">
        <Header dogName={profile.dog_name} location={profile.location} fallback={Boolean(state.status?.fallback)}>
          {/* Station Status Pill: Always visible on mobile and desktop */}
          <button
            type="button"
            onClick={() => openPairModal("station")}
            title={
              state.status?.phone?.connected
                ? "Phone Station is live · tap to manage"
                : stations.length > 0
                ? "Station is paired · tap to manage"
                : "Pair a phone as station camera"
            }
            className={`flex h-10 md:h-9 items-center gap-1.5 px-3 rounded-lg text-xs font-semibold transition-all active:scale-95 shrink-0 shadow-xs ${
              state.status?.phone?.connected
                ? "border border-emerald-500/40 bg-emerald-500/15 text-emerald-300 hover:bg-emerald-500/25"
                : stations.length > 0
                ? "border border-amber-500/40 bg-amber-500/15 text-amber-300 hover:bg-amber-500/25"
                : "border border-accent/40 bg-accent/15 text-accent hover:bg-accent/25"
            }`}
          >
            <span className="relative flex h-2 w-2">
              {state.status?.phone?.connected && (
                <span className="absolute inline-flex h-full w-full rounded-full bg-emerald-400 opacity-75 animate-ping" />
              )}
              <span
                className={`relative inline-flex h-2 w-2 rounded-full ${
                  state.status?.phone?.connected
                    ? "bg-emerald-500"
                    : stations.length > 0
                    ? "bg-amber-500"
                    : "bg-accent"
                }`}
              />
            </span>
            <span className="whitespace-nowrap font-medium">
              {state.status?.phone?.connected
                ? "Station Live"
                : stations.length > 0
                ? "Station Paired"
                : "Pair Phone"}
            </span>
          </button>

          {/* Desktop-only navigation icons (moved to MobileMenuDrawer on phones) */}
          <button
            type="button"
            onClick={() => setProfileOpen((n) => (n ?? 0) + 1)}
            title="Edit pet profile"
            aria-label="Edit pet profile"
            className="hidden lg:flex h-9 w-9 items-center justify-center rounded-lg border border-border text-muted hover:text-white transition-colors"
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
                className="hidden lg:flex h-9 w-9 items-center justify-center rounded-lg border border-border text-muted hover:text-white transition-colors"
              >
                <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true"><path d="M17 21v-2a4 4 0 0 0-4-4H5a4 4 0 0 0-4 4v2"/><circle cx="9" cy="7" r="4"/><path d="M23 21v-2a4 4 0 0 0-3-3.87"/><path d="M16 3.13a4 4 0 0 1 0 7.75"/></svg>
              </button>
              <button
                type="button"
                onClick={() => setDeleteOpen(true)}
                title="Delete household"
                aria-label="Delete household"
                className="hidden lg:flex h-9 w-9 items-center justify-center rounded-lg border border-red-500/40 text-red-300 hover:text-white transition-colors"
              >
                <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true"><path d="M3 6h18"/><path d="M19 6v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6"/><path d="M8 6V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2"/></svg>
              </button>
              <button
                type="button"
                onClick={() => void logoutHousehold()}
                title="Sign out"
                aria-label="Sign out"
                className="hidden lg:flex h-9 w-9 items-center justify-center rounded-lg border border-border text-muted hover:text-white transition-colors"
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
              className="hidden lg:flex h-9 w-9 items-center justify-center rounded-lg border border-border text-muted hover:text-white transition-colors"
            >
              <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
                <rect x="3" y="11" width="18" height="11" rx="2" ry="2" />
                <path d="M7 11V7a5 5 0 0 1 10 0v4" />
              </svg>
            </button>
          )}
          {activeBatteryPct !== null && !activeIsStale && (
            <div
              className={`hidden lg:flex items-center gap-1.5 rounded-lg border px-2.5 py-1 text-[12px] font-mono transition-colors ${
                activeBClass === "critical"
                  ? "border-red-500/40 bg-red-500/20 text-red-400 font-semibold"
                  : activeBClass === "low"
                  ? "border-amber-500/40 bg-amber-500/20 text-amber-400 font-semibold"
                  : "border-border bg-surface text-muted"
              }`}
              title={`Active station battery: ${activeBText}${activeIsCharging ? " (charging)" : ""}${activeIsStale ? " (stale)" : ""}`}
            >
              {activeIsCharging ? (
                <svg
                  width="12"
                  height="12"
                  viewBox="0 0 24 24"
                  fill="currentColor"
                  stroke="currentColor"
                  strokeWidth="1"
                  className="text-amber-400 animate-pulse"
                  aria-label="Charging"
                >
                  <polygon points="13 2 3 14 12 14 11 22 21 10 12 10 13 2" />
                </svg>
              ) : (
                <svg
                  width="12"
                  height="12"
                  viewBox="0 0 24 24"
                  fill="none"
                  stroke="currentColor"
                  strokeWidth="2"
                  strokeLinecap="round"
                  strokeLinejoin="round"
                  aria-hidden="true"
                >
                  <rect x="2" y="7" width="16" height="10" rx="2" ry="2" />
                  <line x1="22" y1="11" x2="22" y2="13" />
                </svg>
              )}
              <span>{activeBText}</span>
            </div>
          )}
          <div className="hidden lg:flex items-center">
            <ThemeToggle />
          </div>
          <NotificationsBell
            notifications={state.notifications}
            dogName={profile.dog_name}
            onOpen={backend.readAll}
            onSelectMoment={onSelectNotification}
          />
          {backend.authStatus?.debug && (
            <button
              type="button"
              onClick={() => setDebugOpen(true)}
              title="Diagnostics & Debug Mode"
              aria-label="Diagnostics & Debug Mode"
              className="hidden lg:flex h-9 w-9 items-center justify-center rounded-lg border border-border text-muted hover:text-white transition-colors"
            >
              <svg
                width="16"
                height="16"
                viewBox="0 0 24 24"
                fill="none"
                stroke="currentColor"
                strokeWidth="2"
                strokeLinecap="round"
                strokeLinejoin="round"
                aria-hidden="true"
              >
                <path d="M12.22 2h-.44a2 2 0 0 0-2 2v.18a2 2 0 0 1-1 1.73l-.43.25a2 2 0 0 1-2 0l-.15-.08a2 2 0 0 0-2.73.73l-.22.38a2 2 0 0 0 .73 2.73l.15.1a2 2 0 0 1 1 1.72v.51a2 2 0 0 1-1 1.74l-.15.09a2 2 0 0 0-.73 2.73l.22.38a2 2 0 0 0 2.73.73l.15-.08a2 2 0 0 1 2 0l.43.25a2 2 0 0 1 1 1.73V20a2 2 0 0 0 2 2h.44a2 2 0 0 0 2-2v-.18a2 2 0 0 1 1-1.73l.43-.25a2 2 0 0 1 2 0l.15.08a2 2 0 0 0 2.73-.73l.22-.39a2 2 0 0 0-.73-2.73l-.15-.08a2 2 0 0 1-1-1.74v-.5a2 2 0 0 1 1-1.74l.15-.09a2 2 0 0 0 .73-2.73l-.22-.38a2 2 0 0 0-2.73-.73l-.15.08a2 2 0 0 1-2 0l-.43-.25a2 2 0 0 1-1-1.73V4a2 2 0 0 0-2-2z" />
                <circle cx="12" cy="12" r="3" />
              </svg>
            </button>
          )}

          {/* Mobile menu trigger */}
          <button
            type="button"
            onClick={() => setMobileMenuOpen(true)}
            aria-label="Open mobile menu"
            className="flex h-10 w-10 md:h-9 md:w-9 items-center justify-center rounded-lg border border-border bg-surface text-text hover:bg-surface-2 transition-colors lg:hidden shrink-0 active:scale-95"
          >
            <svg
              width="20"
              height="20"
              viewBox="0 0 24 24"
              fill="none"
              stroke="currentColor"
              strokeWidth="2"
              strokeLinecap="round"
              strokeLinejoin="round"
              aria-hidden="true"
            >
              <line x1="4" y1="6" x2="20" y2="6" />
              <line x1="4" y1="12" x2="20" y2="12" />
              <line x1="4" y1="18" x2="20" y2="18" />
            </svg>
          </button>
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
                <span className="rounded-full bg-surface-2 px-2 py-0.5 text-[12px] font-semibold text-muted">
                  {stations.length}
                </span>
              </div>
              <button
                type="button"
                onClick={() => {
                  setShowGrid(false);
                  backend.reportFocus(state.focusedStation !== "default" ? state.focusedStation : undefined, "focus");
                }}
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
                backend.reportFocus(id, "focus");
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
                onClick={() => {
                  setShowGrid(true);
                  backend.reportFocus(undefined, "grid");
                }}
                className="flex min-h-11 md:min-h-8 items-center gap-1.5 rounded-lg border border-border bg-surface px-3 py-1 text-small font-semibold text-text hover:bg-surface-2 transition-colors"
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
                blackoutPaused={blackoutPaused}
                stream={shouldStream(st)}
                dropped={st === "dropped"}
                hasPairedStation={stations.length > 0}
                onPair={() => openPairModal("station")}
                lastDogTs={state.lastDogTs}
                privacyMode={privacyMode}
                onTogglePrivacy={() => void setPrivacy()}
                token={token}
                maxIdleSec={state.status?.max_idle_s}
                effectiveMode={backend.effectiveMode}
                stationCap={backend.stationCap}
                householdMode={backend.householdMode}
                modeCeiling={backend.me?.mode_ceiling}
                stationState={st}
                signedIn={backend.authenticated}
                isOwner={!backend.me || backend.me.role === "owner"}
                onSetMode={backend.setViewMode}
                onPeek={backend.peek}
                modeRequestResult={state.status?.phone?.last_mode_request_result ?? null}
                station={state.focusedStation !== "default" ? state.focusedStation : undefined}
                onOpenSoundboard={() => setSoundboardOpen(true)}
              />
            </div>
            <div className="[grid-area:card]">
              <EmotionCard current={state.current} currentSince={state.currentSince} lastEmotionAt={state.lastEmotionAt}
                nowMs={nowMs} nowSec={nowSec} paused={paused} idle={idle} projector={projector} dogName={profile.dog_name}
                lastDogTs={state.lastDogTs} lastSeenEmotion={lastSeenEmotion} />
            </div>
            <div className="hidden [grid-area:treat] lg:flex lg:flex-col lg:gap-2">
              <TreatButton onTreat={onTreat} flash={treatFlash} />
              <div className="flex flex-col gap-1.5 rounded-xl border border-border bg-surface p-2.5 shadow-xs">
                <div className="flex items-center justify-between px-1">
                  <span className="text-[11px] font-bold uppercase tracking-wider text-muted">
                    Quick Attention Cues
                  </span>
                  <button
                    type="button"
                    onClick={() => setSoundboardOpen(true)}
                    className="text-[11px] font-semibold text-accent hover:underline flex items-center gap-1"
                  >
                    <span>All cues 🎛️</span>
                  </button>
                </div>
                <div className="grid grid-cols-2 gap-1.5">
                  <button
                    type="button"
                    onClick={() => triggerQuickCue("squeak", undefined, "Squeaky Toy 🧸")}
                    className={`flex h-10 items-center justify-center gap-1.5 rounded-lg border px-2 text-xs font-semibold transition-all active:scale-95 ${
                      activeCueId === "squeak"
                        ? "border-[#EC4899] bg-[#EC4899]/20 text-[#F472B6]"
                        : "border-border bg-surface-2 text-text hover:bg-surface-3"
                    }`}
                    title="Play high-pitch squeak to get dog's attention"
                  >
                    <span>🧸</span>
                    <span>Squeak</span>
                  </button>
                  <button
                    type="button"
                    onClick={() => triggerQuickCue("doorbell", undefined, "Chime Bell 🔔")}
                    className={`flex h-10 items-center justify-center gap-1.5 rounded-lg border px-2 text-xs font-semibold transition-all active:scale-95 ${
                      activeCueId === "doorbell"
                        ? "border-[#10B981] bg-[#10B981]/20 text-[#34D399]"
                        : "border-border bg-surface-2 text-text hover:bg-surface-3"
                    }`}
                    title="Play ding-dong alert chime"
                  >
                    <span>🔔</span>
                    <span>Chime</span>
                  </button>
                  <button
                    type="button"
                    onClick={() => triggerQuickCue("whistle", undefined, "Focus Whistle 🎵")}
                    className={`flex h-10 items-center justify-center gap-1.5 rounded-lg border px-2 text-xs font-semibold transition-all active:scale-95 ${
                      activeCueId === "whistle"
                        ? "border-[#3B82F6] bg-[#3B82F6]/20 text-[#60A5FA]"
                        : "border-border bg-surface-2 text-text hover:bg-surface-3"
                    }`}
                    title="Play dog focus whistle"
                  >
                    <span>🎵</span>
                    <span>Whistle</span>
                  </button>
                  <button
                    type="button"
                    onClick={() => triggerQuickCue("squeak", "double_blink", "Squeak + Beacon ✨")}
                    className={`flex h-10 items-center justify-center gap-1.5 rounded-lg border px-2 text-xs font-semibold transition-all active:scale-95 ${
                      activeCueId === "squeak_double_blink"
                        ? "border-[#F59E0B] bg-[#F59E0B]/20 text-[#FBBF24]"
                        : "border-border bg-surface-2 text-text hover:bg-surface-3"
                    }`}
                    title="Play squeak and flash light beacon"
                  >
                    <span>✨</span>
                    <span>Flash+Squeak</span>
                  </button>
                </div>
              </div>
            </div>
            <div className="[grid-area:signals]">
              <SignalsPanel history={state.history} defaultOpen={!projector && !isMobile} />
            </div>
          </div>
          {/* Phones get a disclosure for the timeline; desktop shows it inline. */}
          <div id="timeline">
            <button type="button" aria-expanded={timelineOpen} onClick={() => setTimelineOpen((v) => !v)}
              className="mb-2 flex min-h-11 w-full items-center justify-between rounded-md border border-border bg-surface px-4 py-2.5 text-small font-semibold text-text lg:hidden">
              Timeline
              <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true"
                style={{ transform: timelineOpen ? "rotate(180deg)" : "none" }}>
                <path d="M6 9l6 6 6-6" />
              </svg>
            </button>
            <div className={timelineOpen ? "" : "hidden lg:block"}>
              <Timeline
                spans={state.spans}
                audio={state.audio}
                treats={state.treats}
                notifications={state.notifications}
                nowSec={nowSec}
                selected={selectedSpan}
                onSelect={setSelectedSpan}
                onViewMoment={(sp) =>
                  setActiveMoment({
                    emotion: sp.emotion,
                    ts: sp.start,
                    reason: sp.reason,
                    confidence: sp.confidence,
                    source: sp.source,
                    snapshot: sp.snapshot,
                    duration_s: sp.end > sp.start ? sp.end - sp.start : undefined,
                  })
                }
              />
            </div>
            <DoggyDiary
              stationId={state.focusedStation}
              onViewSnapshot={(snap, title) =>
                setActiveMoment({
                  emotion: "happy",
                  ts: Date.now() / 1000,
                  reason: title,
                  confidence: 1.0,
                  source: "fused",
                  snapshot: snap,
                })
              }
            />
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
          onClose={() => {
            setIsPairingOpen(false);
            setInitialStationCount(null);
          }}
          phoneConnected={state.status?.phone?.connected}
          deviceName={state.status?.phone?.device}
          isPaired={Boolean(pairedStation)}
          pairedDeviceName={pairedStation?.name}
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
        <div className="fixed inset-x-0 bottom-0 z-20 flex flex-col gap-1.5 border-t border-border bg-bg/95 p-2 backdrop-blur lg:hidden"
          style={{ paddingBottom: "max(10px, env(safe-area-inset-bottom))" }}>
          <div className="flex gap-2">
            <div className="grow">
              <TreatButton onTreat={onTreat} flash={treatFlash} />
            </div>
            <button
              type="button"
              onClick={() => setSoundboardOpen(true)}
              aria-label="Soundboard and chimes"
              className="flex h-[60px] items-center justify-center gap-1.5 rounded-lg border border-border bg-surface px-4 text-xs font-bold text-text active:scale-95 shadow-xs"
            >
              <span>🔔 Chimes</span>
            </button>
          </div>
          <div className="flex items-center justify-between gap-1.5 overflow-x-auto pb-0.5">
            <button
              type="button"
              onClick={() => triggerQuickCue("squeak", undefined, "Squeak 🧸")}
              className={`flex h-8 flex-1 items-center justify-center gap-1 rounded-md border px-2 text-[11px] font-semibold transition-all active:scale-95 whitespace-nowrap ${
                activeCueId === "squeak"
                  ? "border-[#EC4899] bg-[#EC4899]/20 text-[#F472B6]"
                  : "border-border bg-surface text-text"
              }`}
            >
              <span>🧸 Squeak</span>
            </button>
            <button
              type="button"
              onClick={() => triggerQuickCue("doorbell", undefined, "Chime 🔔")}
              className={`flex h-8 flex-1 items-center justify-center gap-1 rounded-md border px-2 text-[11px] font-semibold transition-all active:scale-95 whitespace-nowrap ${
                activeCueId === "doorbell"
                  ? "border-[#10B981] bg-[#10B981]/20 text-[#34D399]"
                  : "border-border bg-surface text-text"
              }`}
            >
              <span>🔔 Chime</span>
            </button>
            <button
              type="button"
              onClick={() => triggerQuickCue("whistle", undefined, "Whistle 🎵")}
              className={`flex h-8 flex-1 items-center justify-center gap-1 rounded-md border px-2 text-[11px] font-semibold transition-all active:scale-95 whitespace-nowrap ${
                activeCueId === "whistle"
                  ? "border-[#3B82F6] bg-[#3B82F6]/20 text-[#60A5FA]"
                  : "border-border bg-surface text-text"
              }`}
            >
              <span>🎵 Whistle</span>
            </button>
            <button
              type="button"
              onClick={() => triggerQuickCue("squeak", "double_blink", "Flash ✨")}
              className={`flex h-8 flex-1 items-center justify-center gap-1 rounded-md border px-2 text-[11px] font-semibold transition-all active:scale-95 whitespace-nowrap ${
                activeCueId === "squeak_double_blink"
                  ? "border-[#F59E0B] bg-[#F59E0B]/20 text-[#FBBF24]"
                  : "border-border bg-surface text-text"
              }`}
            >
              <span>✨ Flash</span>
            </button>
          </div>
        </div>
      )}
      <ToastStack toasts={state.toasts} onDismiss={backend.dismissToast} onView={viewMoment} />
      <MomentModal
        isOpen={!!activeMoment}
        onClose={() => setActiveMoment(null)}
        dogName={profile.dog_name}
        moment={activeMoment}
      />
      <SoundboardDrawer
        isOpen={soundboardOpen}
        onClose={() => setSoundboardOpen(false)}
        http={backend.http}
        token={token}
        stationId={state.focusedStation !== "default" ? state.focusedStation : undefined}
        dogName={profile.dog_name}
      />
      <DebugDrawer
        open={debugOpen}
        onClose={() => setDebugOpen(false)}
        http={backend.http}
        token={token || undefined}
        stationId={state.focusedStation !== "default" ? state.focusedStation : undefined}
        stationName={state.stations[state.focusedStation]?.name || profile.dog_name}
        llmInfo={{
          provider: state.status?.llm?.provider || undefined,
          model: state.status?.llm?.model || undefined,
        }}
        clockOffset={state.clockOffset}
        eventFps={state.status?.pipeline_status?.fps ?? state.status?.phone?.fps}
        previewFps={state.status?.pipeline_status?.preview_fps}
      />
      <MobileMenuDrawer
        isOpen={mobileMenuOpen}
        onClose={() => setMobileMenuOpen(false)}
        dogName={profile.dog_name}
        location={profile.location}
        stationConnected={Boolean(state.status?.phone?.connected)}
        hasPairedStation={stations.length > 0}
        batteryPct={activeBatteryPct}
        isCharging={activeIsCharging}
        isBatteryStale={activeIsStale}
        hasMultipleStations={hasMultipleStations}
        showGrid={showGrid}
        onOpenProfile={() => setProfileOpen((n) => (n ?? 0) + 1)}
        onOpenMembers={() => setMembersOpen(true)}
        onOpenPairModal={() => openPairModal("station")}
        onOpenDeleteHousehold={() => setDeleteOpen(true)}
        onToggleGrid={() => {
          setShowGrid((g) => !g);
          backend.reportFocus(showGrid ? state.focusedStation : undefined, showGrid ? "focus" : "grid");
        }}
        onLogout={authMode === "households" && authenticated ? logoutHousehold : token ? logout : undefined}
        isOwner={!me || me.role === "owner"}
        themeToggleNode={<ThemeToggle />}
      />
    </div>
  );
}
