"use client";

import { useCallback, useEffect, useMemo, useReducer } from "react";
import { backendBase } from "./config";
import { initialState, reduce } from "./store";
import type { Envelope, Span, Status } from "./types";

const STATUS_POLL_MS = 2000;
const MAX_BACKOFF_MS = 5000;

export function useBackend() {
  const [state, dispatch] = useReducer(reduce, initialState);
  const base = useMemo(() => backendBase(), []);

  useEffect(() => {
    let ws: WebSocket | null = null;
    let stopped = false;
    let attempt = 0;
    let retry: ReturnType<typeof setTimeout> | undefined;

    const getJson = async <T,>(path: string): Promise<T> => {
      const r = await fetch(`${base.http}${path}`, { cache: "no-store" });
      if (!r.ok) throw new Error(`${path}: HTTP ${r.status}`);
      return (await r.json()) as T;
    };

    const bootstrap = async () => {
      try {
        const [ev, st] = await Promise.all([
          getJson<{ events: Envelope[]; timeline: Span[] }>("/events"),
          getJson<Status>("/status"),
        ]);
        if (stopped) return;
        dispatch({ kind: "bootstrap", events: ev.events, timeline: ev.timeline, at: Date.now() });
        dispatch({ kind: "status", status: st });
      } catch {
        /* backend unreachable: the socket retry loop covers it */
      }
    };

    const connect = () => {
      if (stopped) return;
      ws = new WebSocket(`${base.ws}/ws`);
      ws.onopen = () => {
        attempt = 0;
        dispatch({ kind: "connected", value: true });
        void bootstrap();
      };
      ws.onmessage = (m) => {
        try {
          dispatch({ kind: "envelope", env: JSON.parse(String(m.data)) as Envelope, at: Date.now() });
        } catch {
          /* ignore malformed message */
        }
      };
      ws.onerror = () => ws?.close();
      ws.onclose = () => {
        dispatch({ kind: "connected", value: false });
        if (stopped) return;
        attempt += 1;
        retry = setTimeout(connect, Math.min(MAX_BACKOFF_MS, 500 * 2 ** (attempt - 1)));
      };
    };

    connect();
    const poll = setInterval(() => {
      getJson<Status>("/status")
        .then((st) => !stopped && dispatch({ kind: "status", status: st }))
        .catch(() => undefined);
    }, STATUS_POLL_MS);
    const tick = setInterval(() => dispatch({ kind: "tick", at: Date.now() }), 1000);

    return () => {
      stopped = true;
      clearTimeout(retry);
      clearInterval(poll);
      clearInterval(tick);
      if (ws) {
        ws.onclose = null;
        ws.close();
      }
    };
  }, [base]);

  const treat = useCallback(async () => {
    await fetch(`${base.http}/treat`, { method: "POST" }).catch(() => undefined);
  }, [base]);

  const setMode = useCallback(async (mode: "live" | "demo") => {
    await fetch(`${base.http}/mode`, {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ mode }),
    }).catch(() => undefined);
  }, [base]);

  const readAll = useCallback(() => dispatch({ kind: "readAll" }), []);
  const dismissToast = useCallback((id: string) => dispatch({ kind: "dismissToast", id }), []);

  return { state, treat, readAll, setMode, dismissToast, http: base.http };
}
