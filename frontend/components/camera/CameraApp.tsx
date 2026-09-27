"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { backendBase } from "@/lib/config";
import { type Facing, mediaErrorKind, parseFps } from "@/lib/ingest";
import { createAudioContext, openMedia, stopStream } from "@/lib/media";
import CameraBlocked from "./CameraBlocked";
import CameraSetup from "./CameraSetup";
import CameraStreaming from "./CameraStreaming";

type Phase = "setup" | "blocked" | "streaming";

function errorText(kind: string, name: string): string {
  if (kind === "insecure") return "Camera access needs HTTPS. Open this page through the https:// tunnel link.";
  if (kind === "busy") return "The camera is in use by another app. Close it, then tap Allow access again.";
  return `Couldn’t start the camera (${name || "unknown error"}).`;
}

export default function CameraApp() {
  const base = useMemo(() => backendBase(), []);
  const fps = useMemo(() => parseFps(window.location.search), []);
  const backendHost = useMemo(() => {
    try {
      return new URL(base.http).host;
    } catch {
      return base.http;
    }
  }, [base]);

  const [phase, setPhase] = useState<Phase>("setup");
  const [facing, setFacing] = useState<Facing>("back");
  const [stream, setStream] = useState<MediaStream | null>(null);
  const [camera, setCamera] = useState(true);
  const [audioCtx, setAudioCtx] = useState<AudioContext | null>(null);
  const [requesting, setRequesting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [dogName, setDogName] = useState("your dog");
  const [reachable, setReachable] = useState<boolean | null>(null);
  const streamRef = useRef<MediaStream | null>(null);

  const replaceStream = useCallback((next: MediaStream | null) => {
    if (streamRef.current && streamRef.current !== next) stopStream(streamRef.current);
    streamRef.current = next;
    setStream(next);
  }, []);

  const acquire = useCallback(async (f: Facing) => {
    setRequesting(true);
    setError(null);
    replaceStream(null); // iOS can't open a second camera while the first is live
    try {
      replaceStream(await openMedia(f, true));
      setCamera(true);
      setPhase("setup");
    } catch (e) {
      const name = (e as { name?: string } | null)?.name ?? "";
      const kind = mediaErrorKind(name, window.isSecureContext);
      if (kind === "blocked") setPhase("blocked");
      else setError(errorText(kind, name));
    } finally {
      setRequesting(false);
    }
  }, [replaceStream]);

  // Dog name for the headline + a reachability check for the footer.
  useEffect(() => {
    let alive = true;
    fetch(`${base.http}/status`, { cache: "no-store" })
      .then((r) => (r.ok ? r.json() : Promise.reject(new Error(String(r.status)))))
      .then((s: { profile?: { dog_name?: string } }) => {
        if (!alive) return;
        setReachable(true);
        if (s.profile?.dog_name) setDogName(s.profile.dog_name);
      })
      .catch(() => alive && setReachable(false));
    return () => {
      alive = false;
    };
  }, [base]);

  // Reopening the page after permission was granted once: skip the "Allow access" tap.
  useEffect(() => {
    let alive = true;
    navigator.permissions
      ?.query({ name: "camera" as PermissionName })
      .then((p) => {
        if (alive && p.state === "granted") void acquire("back");
      })
      .catch(() => undefined);
    return () => {
      alive = false;
    };
  }, [acquire]);

  const audioRef = useRef<AudioContext | null>(null);
  const replaceAudio = (next: AudioContext | null) => {
    if (audioRef.current && audioRef.current !== next) void audioRef.current.close().catch(() => undefined);
    audioRef.current = next;
    setAudioCtx(next);
  };

  useEffect(() => () => {
    stopStream(streamRef.current);
    void audioRef.current?.close().catch(() => undefined);
  }, []);

  const pick = (f: Facing) => {
    setFacing(f);
    if (streamRef.current && camera) void acquire(f);
  };

  const start = () => {
    if (!streamRef.current) return;
    replaceAudio(createAudioContext()); // inside the tap: iOS only starts audio from a user gesture
    setPhase("streaming");
  };

  const micOnly = async () => {
    const ctx = createAudioContext(); // before the await, still inside the tap
    setError(null);
    try {
      replaceStream(await openMedia(facing, false));
      setCamera(false);
      replaceAudio(ctx);
      setPhase("streaming");
    } catch {
      void ctx?.close().catch(() => undefined);
      setError("The microphone is blocked too. Allow it in the site settings, then tap Try again.");
    }
  };

  const stop = () => {
    replaceAudio(null); // closes the context; the streaming view unmounts and stops everything else
    if (!camera) replaceStream(null); // mic-only: release the microphone too
    setPhase(camera ? "setup" : "blocked");
  };

  if (phase === "streaming" && stream) {
    return <CameraStreaming stream={stream} audioCtx={audioCtx} camera={camera} facing={facing} fps={fps} wsBase={base.ws} onStop={stop} />;
  }
  if (phase === "blocked") {
    return <CameraBlocked dogName={dogName} error={error} onRetry={() => void acquire(facing)} onMicOnly={() => void micOnly()} />;
  }
  return (
    <CameraSetup dogName={dogName} facing={facing} stream={camera ? stream : null} requesting={requesting} error={error}
      backendHost={backendHost} reachable={reachable} onAllow={() => void acquire(facing)} onPick={pick} onStart={start} />
  );
}
