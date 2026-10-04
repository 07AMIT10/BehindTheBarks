"use client";

import { useCallback, useEffect, useMemo, useReducer, useState } from "react";
import { backendBase } from "./config";
import { initialState, reduce } from "./store";
import type { Envelope, Span, Status } from "./types";

const STATUS_POLL_MS = 2000;
const MAX_BACKOFF_MS = 5000;
const STORAGE_KEY = "btb_access_token";

export function getStoredToken(): string | null {
  if (typeof window === "undefined") return null;
  return localStorage.getItem(STORAGE_KEY) || null;
}

export function setStoredToken(token: string): void {
  if (typeof window === "undefined") return;
  localStorage.setItem(STORAGE_KEY, token);
}

export function clearStoredToken(): void {
  if (typeof window === "undefined") return;
  localStorage.removeItem(STORAGE_KEY);
}

export function useBackend() {
  const [state, dispatch] = useReducer(reduce, initialState);
  const base = useMemo(() => backendBase(), []);

  const [token, setToken] = useState<string | null>(getStoredToken());
  const [authRequired, setAuthRequired] = useState(false);
  const [authenticated, setAuthenticated] = useState(true);
  const [privacyOverride, setPrivacyOverride] = useState<boolean | null>(null);

  const statusPrivacy = Boolean(
    state.status?.pipeline_status?.privacy_mode ?? state.status?.privacy_mode ?? false
  );
  const privacyMode = privacyOverride !== null ? privacyOverride : statusPrivacy;

  // Check auth requirement from server
  useEffect(() => {
    fetch(`${base.http}/auth/status`)
      .then((r) => r.json())
      .then((data) => {
        if (data.auth_required) {
          setAuthRequired(true);
          const current = getStoredToken();
          if (!current) {
            setAuthenticated(false);
          }
        } else {
          setAuthRequired(false);
          setAuthenticated(true);
        }
      })
      .catch(() => undefined);
  }, [base]);

  useEffect(() => {
    let ws: WebSocket | null = null;
    let stopped = false;
    let attempt = 0;
    let retry: ReturnType<typeof setTimeout> | undefined;

    const activeToken = token ?? getStoredToken();

    const getHeaders = (): HeadersInit => {
      const h: Record<string, string> = {};
      if (activeToken) h["Authorization"] = `Bearer ${activeToken}`;
      return h;
    };

    const getJson = async <T,>(path: string): Promise<T> => {
      const sep = path.includes("?") ? "&" : "?";
      const fullUrl = `${base.http}${path}${activeToken ? `${sep}token=${encodeURIComponent(activeToken)}` : ""}`;
      const r = await fetch(fullUrl, { headers: getHeaders(), cache: "no-store" });
      if (r.status === 401) {
        setAuthRequired(true);
        setAuthenticated(false);
        throw new Error(`${path}: HTTP 401 Unauthorized`);
      }
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
      const wsUrl = `${base.ws}/ws${activeToken ? `?token=${encodeURIComponent(activeToken)}` : ""}`;
      ws = new WebSocket(wsUrl);
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
      ws.onclose = (ev) => {
        dispatch({ kind: "connected", value: false });
        if (ev.code === 4401) {
          setAuthRequired(true);
          setAuthenticated(false);
          return;
        }
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
  }, [base, token]);

  const verifyPin = useCallback(async (pin: string): Promise<{ ok: boolean; error?: string }> => {
    try {
      const res = await fetch(`${base.http}/auth/verify`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ pin }),
      });
      if (!res.ok) {
        return { ok: false, error: "Invalid PIN or access token" };
      }
      const data = await res.json();
      const validToken = data.token || pin;
      setStoredToken(validToken);
      setToken(validToken);
      setAuthenticated(true);
      setAuthRequired(false);
      return { ok: true };
    } catch (e: unknown) {
      const err = e as Error;
      return { ok: false, error: err?.message || "Connection error" };
    }
  }, [base]);

  const logout = useCallback(() => {
    clearStoredToken();
    setToken(null);
    setAuthenticated(false);
    setAuthRequired(true);
  }, []);

  const treat = useCallback(async () => {
    const tok = token ?? getStoredToken();
    const headers: Record<string, string> = {};
    if (tok) headers["Authorization"] = `Bearer ${tok}`;
    const url = `${base.http}/treat${tok ? `?token=${encodeURIComponent(tok)}` : ""}`;
    await fetch(url, { method: "POST", headers }).catch(() => undefined);
  }, [base, token]);

  const setMode = useCallback(async (mode: "live" | "demo") => {
    const tok = token ?? getStoredToken();
    const headers: Record<string, string> = { "content-type": "application/json" };
    if (tok) headers["Authorization"] = `Bearer ${tok}`;
    const url = `${base.http}/mode${tok ? `?token=${encodeURIComponent(tok)}` : ""}`;
    await fetch(url, {
      method: "POST",
      headers,
      body: JSON.stringify({ mode }),
    }).catch(() => undefined);
  }, [base, token]);

  const setPrivacy = useCallback(async (enabled?: boolean) => {
    const tok = token ?? getStoredToken();
    const headers: Record<string, string> = { "content-type": "application/json" };
    if (tok) headers["Authorization"] = `Bearer ${tok}`;
    const url = `${base.http}/privacy${tok ? `?token=${encodeURIComponent(tok)}` : ""}`;
    const target = enabled !== undefined ? enabled : !privacyMode;
    setPrivacyOverride(target);
    try {
      const res = await fetch(url, {
        method: "POST",
        headers,
        body: JSON.stringify({ enabled: target }),
      });
      if (res.ok) {
        const data = await res.json();
        setPrivacyOverride(Boolean(data.privacy_mode));
      } else {
        setPrivacyOverride(null);
      }
    } catch {
      setPrivacyOverride(null);
    }
  }, [base, token, privacyMode]);

  const readAll = useCallback(() => dispatch({ kind: "readAll" }), []);
  const dismissToast = useCallback((id: string) => dispatch({ kind: "dismissToast", id }), []);

  return {
    state,
    treat,
    readAll,
    setMode,
    dismissToast,
    http: base.http,
    token,
    authRequired,
    authenticated,
    verifyPin,
    logout,
    privacyMode,
    setPrivacy,
  };
}
