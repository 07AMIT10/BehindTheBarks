"use client";

// React glue for the Listen control: owns one AudioPlayer, the connection reducer and the watchdog.
// Everything that decides (availability, transitions, close-code texts) is in listenState.ts.
import { useCallback, useEffect, useReducer, useRef, useState } from "react";
import { backendBase } from "./config";
import { AudioPlayer, type PlayerStats, audioSocketUrl, browserFactories } from "./audioPlayer";
import {
  type ListenAvailability,
  type ListenWatchdog,
  createListenWatchdog,
  listenAvailability,
  listenReducer,
  listenStatusText,
} from "./listenState";
import type { StationState } from "./phone";
import type { ViewMode } from "./types";

const ACTIVITY_EVENTS = ["mousemove", "mousedown", "keydown", "touchstart", "scroll"] as const;

export interface UseListenOptions {
  effectiveMode: ViewMode;
  viewerCeiling: ViewMode;
  stationState: StationState;
  signedIn: boolean;
  token?: string | null;
  station?: string;
  maxIdleSec?: number;
}

export interface ListenControl {
  availability: ListenAvailability;
  listening: boolean;
  failed: boolean;
  statusText: string;
  stats: PlayerStats | null;
  toggle: () => void;
  stop: () => void;
}

export function useListen(opts: UseListenOptions): ListenControl {
  const { effectiveMode, viewerCeiling, stationState, signedIn, token, station, maxIdleSec = 180 } = opts;
  const [tabVisible, setTabVisible] = useState(true);
  const [state, dispatch] = useReducer(listenReducer, { kind: "idle" as const });
  const [stats, setStats] = useState<PlayerStats | null>(null);
  const playerRef = useRef<AudioPlayer | null>(null);
  const watchdogRef = useRef<ListenWatchdog | null>(null);
  const detachRef = useRef<(() => void) | null>(null);

  const availability: ListenAvailability = signedIn
    ? listenAvailability({ effectiveMode, viewerCeiling, stationState, tabVisible })
    : "hidden";

  const stop = useCallback(() => {
    detachRef.current?.();
    detachRef.current = null;
    watchdogRef.current?.stop();
    watchdogRef.current = null;
    const player = playerRef.current;
    playerRef.current = null;
    player?.stop();
    dispatch({ type: "stop" });
    setStats(null);
  }, []);

  const start = useCallback(() => {
    if (playerRef.current) return;
    const url = audioSocketUrl(backendBase(), { station, token });
    const player = new AudioPlayer({
      url,
      station,
      factories: browserFactories(),
      onStatus: setStats,
      onHello: () => dispatch({ type: "hello" }),
      onRetry: () => dispatch({ type: "retrying" }),
      onEnd: (end) => {
        if (playerRef.current !== player) return;
        playerRef.current = null;
        detachRef.current?.();
        detachRef.current = null;
        watchdogRef.current?.stop();
        watchdogRef.current = null;
        if (end.reason === "unsupported") dispatch({ type: "unsupported" });
        else dispatch({ type: "closed", code: end.code, willRetry: false });
      },
    });
    playerRef.current = player;
    dispatch({ type: "start" });
    // Browsers allow sound only inside the tap: create, resume and unmute synchronously here.
    player.start();
    if (playerRef.current !== player) return; // ended synchronously (no audio support)
    player.setMuted(false);

    const wd = createListenWatchdog(() => stop(), maxIdleSec);
    watchdogRef.current = wd;
    wd.start(); // a hidden tab can stop the player right here
    if (playerRef.current !== player) return;
    const onActivity = () => wd.activity();
    const onVisibility = () => wd.visibility();
    for (const ev of ACTIVITY_EVENTS) window.addEventListener(ev, onActivity, { passive: true });
    document.addEventListener("visibilitychange", onVisibility);
    detachRef.current = () => {
      for (const ev of ACTIVITY_EVENTS) window.removeEventListener(ev, onActivity);
      document.removeEventListener("visibilitychange", onVisibility);
    };
  }, [station, token, maxIdleSec, stop]);

  // Hidden tab, a station that stopped, a mode below Full, or sign-out: stop at once.
  useEffect(() => {
    if (availability !== "ready" && playerRef.current) stop();
  }, [availability, stop]);

  useEffect(() => {
    const onVisibility = () => setTabVisible(document.visibilityState !== "hidden");
    document.addEventListener("visibilitychange", onVisibility);
    return () => document.removeEventListener("visibilitychange", onVisibility);
  }, []);

  // Unmounting (or signing out of the page) releases the socket and the audio context.
  useEffect(() => () => stop(), [stop]);

  const toggle = useCallback(() => {
    if (playerRef.current) stop();
    else start();
  }, [start, stop]);

  const listening = state.kind === "connecting" || state.kind === "listening";
  return {
    availability,
    listening,
    failed: state.kind === "error",
    statusText: listenStatusText(state),
    stats: listening ? stats : null,
    toggle,
    stop,
  };
}
