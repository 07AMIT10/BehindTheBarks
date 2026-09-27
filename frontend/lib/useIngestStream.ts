"use client";

import { type RefObject, useEffect, useState } from "react";
import {
  type Facing, type Quality, audioMessage, buildHello, connectionQuality, countSince, deviceName,
  fitLongSide, micLevel, ratePerSecond, rms,
} from "./ingest";
import { IngestSocket, type IngestState, type IngestStatus } from "./ingestSocket";
import { keepScreenOn, startAudioCapture, startFrameCapture } from "./media";

export type StreamStats = {
  status: IngestStatus;
  message: string | null;
  sentFps: number;
  quality: Quality;
  level: number; // mic level 0..1
  elapsedMs: number;
};

export type IngestStreamOptions = {
  stream: MediaStream;
  audioCtx: AudioContext | null; // created in the Start tap; its owner (CameraApp) closes it on Stop
  video: RefObject<HTMLVideoElement | null>;
  camera: boolean; // false: mic only
  facing: Facing;
  fps: number;
  wsBase: string; // backendBase().ws
};

const UI_MS = 250;
const INITIAL: StreamStats = {
  status: "connecting", message: null, sentFps: 0, quality: { bars: 0, label: "Offline" }, level: 0, elapsedMs: 0,
};

/** Runs the whole phone -> /ingest stream while mounted and reports stats for the streaming view. */
export function useIngestStream(o: IngestStreamOptions): StreamStats {
  const { stream, audioCtx, video, camera, facing, fps, wsBase } = o;
  const [stats, setStats] = useState<StreamStats>(INITIAL);

  useEffect(() => {
    let cancelled = false;
    const startedAt = Date.now();
    const sentTimes: number[] = [];
    let sentWindow = 0;
    let skippedWindow = 0;
    let level = 0;
    let sock: IngestState = { status: "connecting", attempt: 0, reconnects: [], message: null };
    const sampleRate = audioCtx?.sampleRate ?? 48000;

    const helloSize = () => {
      const v = video.current;
      const s = stream.getVideoTracks()[0]?.getSettings();
      return fitLongSide(v?.videoWidth || s?.width || 0, v?.videoHeight || s?.height || 0);
    };

    const socket = new IngestSocket({
      url: `${wsBase}/ingest`,
      hello: () => {
        const { width, height } = helloSize();
        return buildHello({ width, height, fps, sampleRate, device: deviceName(navigator.userAgent), facing, camera });
      },
      onState: (s) => {
        sock = s;
      },
    });
    socket.start();

    const stopFrames = camera
      ? startFrameCapture({
          video: () => video.current,
          fps,
          isOpen: () => socket.isOpen,
          bufferedAmount: () => socket.bufferedAmount,
          send: (m) => socket.send(m),
          onSent: () => {
            sentTimes.push(Date.now());
            if (sentTimes.length > 64) sentTimes.shift();
            sentWindow++;
          },
          onSkip: () => {
            skippedWindow++;
          },
        })
      : () => undefined;

    let stopAudio: () => void = () => undefined;
    if (audioCtx && stream.getAudioTracks().length > 0) {
      startAudioCapture(audioCtx, stream, (samples) => {
        level = micLevel(rms(samples));
        socket.send(audioMessage(samples));
      })
        .then((stop) => {
          if (cancelled) stop();
          else stopAudio = stop;
        })
        .catch(() => undefined);
    }

    const releaseWake = keepScreenOn();

    let lastWindowReset = startedAt;
    let skippedRatio = 0;
    const ui = setInterval(() => {
      const now = Date.now();
      if (now - lastWindowReset >= 2000) {
        const total = sentWindow + skippedWindow;
        skippedRatio = total > 0 ? skippedWindow / total : 0;
        sentWindow = 0;
        skippedWindow = 0;
        lastWindowReset = now;
      }
      setStats({
        status: sock.status,
        message: sock.message,
        sentFps: ratePerSecond(sentTimes, now),
        quality: connectionQuality({
          connected: sock.status === "open",
          bufferedAmount: socket.bufferedAmount,
          reconnectsLastMinute: countSince(sock.reconnects, now, 60_000),
          skippedRatio,
        }),
        level,
        elapsedMs: now - startedAt,
      });
    }, UI_MS);

    return () => {
      cancelled = true;
      clearInterval(ui);
      stopFrames();
      stopAudio();
      releaseWake();
      socket.stop();
    };
  }, [stream, audioCtx, video, camera, facing, fps, wsBase]);

  return stats;
}
