"use client";

import { useCallback, useEffect, useRef, useState } from "react";

export interface UseTalkOptions {
  http: string;
  token?: string | null;
  stationId?: string;
  disabled?: boolean;
}

export interface TalkControl {
  talking: boolean;
  /** True from startTalk() until the mic graph is live or the attempt ends: covers the permission-dialog window. */
  starting: boolean;
  error: string | null;
  startTalk: () => Promise<void>;
  stopTalk: () => void;
}

export function useTalk(opts: UseTalkOptions): TalkControl {
  const { http, token, stationId, disabled = false } = opts;
  const [talking, setTalking] = useState(false);
  const [starting, setStarting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const audioCtxRef = useRef<AudioContext | null>(null);
  const streamRef = useRef<MediaStream | null>(null);
  const processorRef = useRef<ScriptProcessorNode | null>(null);
  const bufferRef = useRef<number[]>([]);
  const isTalkingRef = useRef(false);
  /** startTalk is awaiting getUserMedia / graph connect: a stopTalk during it latches here. */
  const startingRef = useRef(false);
  const wantStopRef = useRef(false);

  const flushBuffer = useCallback(
    async (final: boolean = false) => {
      const samples = bufferRef.current;
      if (samples.length === 0) return;
      if (!final && samples.length < 4000) return; // ~250ms chunks at 16kHz

      const pcm16 = new Int16Array(samples.length);
      for (let i = 0; i < samples.length; i++) {
        pcm16[i] = samples[i];
      }
      bufferRef.current = [];

      // Convert to Base64
      const bytes = new Uint8Array(pcm16.buffer, pcm16.byteOffset, pcm16.byteLength);
      let binary = "";
      for (let i = 0; i < bytes.byteLength; i++) {
        binary += String.fromCharCode(bytes[i]);
      }
      const b64 = btoa(binary);

      try {
        const tokenParam = token ? `?token=${encodeURIComponent(token)}` : "";
        const headers: Record<string, string> = { "Content-Type": "application/json" };
        if (token) headers["Authorization"] = `Bearer ${token}`;

        const controller = new AbortController();
        const timer = setTimeout(() => controller.abort(), 4000);
        try {
          await fetch(`${http}/voice/chunk${tokenParam}`, {
            method: "POST",
            headers,
            credentials: "include",
            body: JSON.stringify({
              pcm: b64,
              station: stationId && stationId !== "default" ? stationId : undefined,
            }),
            signal: controller.signal,
          });
        } finally {
          clearTimeout(timer);
        }
      } catch (err) {
        console.warn("Failed to stream voice chunk", err);
      }
    },
    [http, token, stationId]
  );

  const teardown = useCallback(() => {
    if (processorRef.current) {
      processorRef.current.disconnect();
      processorRef.current = null;
    }
    if (streamRef.current) {
      streamRef.current.getTracks().forEach((t) => t.stop());
      streamRef.current = null;
    }
    if (audioCtxRef.current) {
      try {
        void audioCtxRef.current.close();
      } catch {}
      audioCtxRef.current = null;
    }
    bufferRef.current = [];
  }, []);

  const stopTalk = useCallback(() => {
    if (!isTalkingRef.current) {
      // Mic graph not up yet (permission dialog open, getUserMedia pending): latch the
      // release so the teardown runs the moment startTalk finishes, instead of letting
      // the mic stream into /voice/chunk until the tab dies.
      if (startingRef.current) wantStopRef.current = true;
      return;
    }
    isTalkingRef.current = false;
    setTalking(false);

    void flushBuffer(true);
    teardown();
  }, [flushBuffer, teardown]);

  const startTalk = useCallback(async () => {
    if (disabled || isTalkingRef.current || startingRef.current) return;
    startingRef.current = true;
    wantStopRef.current = false;
    setStarting(true);
    setError(null);

    try {
      if (typeof window === "undefined" || !navigator.mediaDevices?.getUserMedia) {
        throw new Error("Microphone access is not supported on this browser.");
      }

      const stream = await navigator.mediaDevices.getUserMedia({
        audio: {
          channelCount: 1,
          echoCancellation: true,
          noiseSuppression: true,
          autoGainControl: true,
        },
      });

      streamRef.current = stream;

      const AudioCtx =
        window.AudioContext ||
        (window as unknown as { webkitAudioContext: typeof AudioContext }).webkitAudioContext;
      const ctx = new AudioCtx();
      audioCtxRef.current = ctx;

      const source = ctx.createMediaStreamSource(stream);
      // 4096 buffer size
      const processor = ctx.createScriptProcessor(4096, 1, 1);
      processorRef.current = processor;

      const inputRate = ctx.sampleRate;
      const targetRate = 16000;
      const ratio = inputRate / targetRate;

      processor.onaudioprocess = (e) => {
        if (!isTalkingRef.current) return;
        const inputData = e.inputBuffer.getChannelData(0);

        // Resample to 16kHz
        if (inputRate === targetRate) {
          for (let i = 0; i < inputData.length; i++) {
            const s = Math.max(-1, Math.min(1, inputData[i]));
            bufferRef.current.push(s < 0 ? s * 0x8000 : s * 0x7fff);
          }
        } else {
          const outCount = Math.floor(inputData.length / ratio);
          for (let i = 0; i < outCount; i++) {
            const pos = i * ratio;
            const idx = Math.floor(pos);
            const frac = pos - idx;
            const s0 = inputData[idx] ?? 0;
            const s1 = inputData[idx + 1] ?? s0;
            const sample = s0 + frac * (s1 - s0);
            const s = Math.max(-1, Math.min(1, sample));
            bufferRef.current.push(s < 0 ? s * 0x8000 : s * 0x7fff);
          }
        }

        void flushBuffer(false);
      };

      source.connect(processor);
      processor.connect(ctx.destination);

      isTalkingRef.current = true;
      setTalking(true);
    } catch (err) {
      console.error("Failed to start voice transmission", err);
      setError(err instanceof Error ? err.message : "Microphone permission denied");
      // Partial start (e.g. mic granted but the graph failed to build): stop the tracks now.
      teardown();
      isTalkingRef.current = false;
      setTalking(false);
    } finally {
      // A release during the pending window tears the fresh mic graph down immediately.
      if (wantStopRef.current) {
        wantStopRef.current = false;
        isTalkingRef.current = false;
        setTalking(false);
        teardown();
      }
      startingRef.current = false;
      setStarting(false);
    }
  }, [disabled, flushBuffer, teardown]);

  useEffect(() => {
    return () => {
      stopTalk();
    };
  }, [stopTalk]);

  return {
    talking,
    starting,
    error,
    startTalk,
    stopTalk,
  };
}
