"use client";

import { useCallback, useEffect, useMemo, useReducer, useRef, useState } from "react";
import { authedHeaders, authedUrl, revokeViewerRequest, revokeStationRequest } from "./httpAuth";
import type { AuthStatus } from "./household";
import { backendBase } from "./config";
import { initialState, reduce } from "./store";
import type { Envelope, Span, Status, ViewMode } from "./types";

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

export async function fetchWithTimeout(url: string, init: RequestInit = {}, timeoutMs = 8000): Promise<Response> {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), timeoutMs);
  const signal = init.signal ?? controller.signal;
  try {
    return await fetch(url, { ...init, signal });
  } finally {
    clearTimeout(timer);
  }
}

export function useBackend() {
  const [state, dispatch] = useReducer(reduce, initialState);
  const base = useMemo(() => backendBase(), []);

  const [token, setToken] = useState<string | null>(getStoredToken());
  const [authRequired, setAuthRequired] = useState(false);
  const [authStatus, setAuthStatus] = useState<AuthStatus | null>(null);
  const [me, setMe] = useState<{ household_id: string; role: string; mode_ceiling?: ViewMode } | null>(null);
  const [authenticated, setAuthenticated] = useState(true);
  const [privacyOverride, setPrivacyOverride] = useState<boolean | null>(null);

  const statusPrivacy = Boolean(
    state.status?.pipeline_status?.privacy_mode ?? state.status?.privacy_mode ?? false
  );
  const privacyMode = privacyOverride !== null ? privacyOverride : statusPrivacy;

  const effectiveMode: ViewMode =
    state.status?.mode_info?.effective ??
    state.status?.effective_mode ??
    state.status?.pipeline_status?.effective_mode ??
    (privacyMode ? "blackout" : "full");

  const stationCap: ViewMode =
    state.status?.mode_info?.station_cap ??
    state.status?.station_cap ??
    state.status?.pipeline_status?.station_cap ??
    "full";

  const householdMode: ViewMode =
    state.status?.mode_info?.household_mode ??
    state.status?.pipeline_status?.policy_mode ??
    "full";

  // Check auth requirement from server. In pin mode the localStorage token decides (Bearer, and the
  // localStorage token is still written by AuthModal). In households mode the HttpOnly cookie decides:
  // no localStorage token is involved, and a 401 on any request means "show the Welcome screen".
  useEffect(() => {
    fetchWithTimeout(`${base.http}/auth/status`, { credentials: "include" }, 6000)
      .then((r) => r.json())
      .then((data: AuthStatus) => {
        setAuthStatus(data);
        if (data.mode === "households") {
          setAuthRequired(Boolean(data.auth_required));
          if (!data.auth_required) {
            setAuthenticated(true);
            return;
          }
          // The session cookie is HttpOnly: probe /me once to learn whether we have one (and who).
          void fetchWithTimeout(`${base.http}/me`, { credentials: "include" }, 6000)
            .then(async (r) => {
              setAuthenticated(r.ok);
              if (r.ok) setMe(await r.json());
            })
            .catch(() => setAuthenticated(false));
          return;
        }
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

    const getJson = async <T,>(path: string, timeoutMs = 8000): Promise<T> => {
      const sep = path.includes("?") ? "&" : "?";
      const fullUrl = `${base.http}${path}${activeToken ? `${sep}token=${encodeURIComponent(activeToken)}` : ""}`;
      const r = await fetchWithTimeout(fullUrl, { headers: getHeaders(), credentials: "include", cache: "no-store" }, timeoutMs);
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
        const st = await getJson<Status>("/status", 6000);
        if (stopped) return;
        dispatch({ kind: "status", status: st });

        const stations = st.stations && st.stations.length > 0 ? st.stations : [{ id: "default" }];
        await Promise.all(
          stations.map(async (s) => {
            const evPath = s.id === "default" ? "/events" : `/events?station=${encodeURIComponent(s.id)}`;
            try {
              const ev = await getJson<{ events: Envelope[]; timeline: Span[] }>(evPath, 6000);
              if (!stopped) {
                dispatch({ kind: "bootstrap", stationId: s.id, events: ev.events, timeline: ev.timeline, at: Date.now() });
              }
            } catch {
              /* ignore per-station events failure */
            }
          })
        );
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

    // Sequential self-scheduling poll that never queues up multiple requests concurrently
    let isPolling = false;
    let pollTimer: ReturnType<typeof setTimeout> | undefined;

    const schedulePoll = (delayMs: number) => {
      if (stopped) return;
      clearTimeout(pollTimer);
      pollTimer = setTimeout(async () => {
        if (stopped) return;
        // Pause/back-off when tab is in background (e.g. mobile phone locked)
        if (typeof document !== "undefined" && document.visibilityState === "hidden") {
          schedulePoll(15000);
          return;
        }
        const isWsConnected = ws?.readyState === WebSocket.OPEN;
        const nextDelay = isWsConnected ? 8000 : STATUS_POLL_MS;

        if (isPolling) {
          schedulePoll(nextDelay);
          return;
        }

        isPolling = true;
        try {
          const st = await getJson<Status>("/status", 6000);
          if (!stopped) {
            dispatch({ kind: "status", status: st });
          }
        } catch {
          // ignore transient poll failure
        } finally {
          isPolling = false;
          schedulePoll(nextDelay);
        }
      }, delayMs);
    };

    schedulePoll(STATUS_POLL_MS);

    const onVisibilityChange = () => {
      if (typeof document !== "undefined" && document.visibilityState === "visible" && !stopped) {
        schedulePoll(50);
      }
    };
    if (typeof document !== "undefined") {
      document.addEventListener("visibilitychange", onVisibilityChange);
    }

    const tick = setInterval(() => dispatch({ kind: "tick", at: Date.now() }), 1000);

    return () => {
      stopped = true;
      clearTimeout(retry);
      clearTimeout(pollTimer);
      clearInterval(tick);
      if (typeof document !== "undefined") {
        document.removeEventListener("visibilitychange", onVisibilityChange);
      }
      if (ws) {
        ws.onclose = null;
        ws.close();
      }
    };
  }, [base, token]);

  const verifyPin = useCallback(async (pin: string): Promise<{ ok: boolean; error?: string }> => {
    try {
      const res = await fetchWithTimeout(`${base.http}/auth/verify`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ pin }),
      }, 6000);
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
    await fetchWithTimeout(url, { method: "POST", headers }, 4000).catch(() => undefined);
  }, [base, token]);

  const setMode = useCallback(async (mode: "live" | "demo") => {
    const tok = token ?? getStoredToken();
    const headers: Record<string, string> = { "content-type": "application/json" };
    if (tok) headers["Authorization"] = `Bearer ${tok}`;
    const url = `${base.http}/mode${tok ? `?token=${encodeURIComponent(tok)}` : ""}`;
    await fetchWithTimeout(url, {
      method: "POST",
      headers,
      body: JSON.stringify({ mode }),
    }, 5000).catch(() => undefined);
  }, [base, token]);

  const setViewMode = useCallback(
    async (mode: ViewMode): Promise<{ status: "applied" | "pending"; mode: ViewMode } | undefined> => {
      const tok = token ?? getStoredToken();
      const headers: Record<string, string> = { "content-type": "application/json" };
      if (tok) headers["Authorization"] = `Bearer ${tok}`;
      const url = `${base.http}/mode${tok ? `?token=${encodeURIComponent(tok)}` : ""}`;
      try {
        const res = await fetchWithTimeout(url, {
          method: "POST",
          headers,
          body: JSON.stringify({ mode }),
        }, 5000);
        if (res.ok) {
          return await res.json();
        }
      } catch {
        // ignore
      }
      return undefined;
    },
    [base, token]
  );

  const peek = useCallback(async (): Promise<boolean> => {
    const tok = token ?? getStoredToken();
    const headers: Record<string, string> = { "content-type": "application/json" };
    if (tok) headers["Authorization"] = `Bearer ${tok}`;
    const url = `${base.http}/peek${tok ? `?token=${encodeURIComponent(tok)}` : ""}`;
    try {
      const res = await fetchWithTimeout(url, { method: "POST", headers }, 5000);
      return res.ok;
    } catch {
      return false;
    }
  }, [base, token]);

  const authMode = authStatus?.mode ?? "pin";

  const createHousehold = useCallback(async (v: { dog_name: string; location: string }) => {
    const res = await fetchWithTimeout(`${base.http}/households`, {
      method: "POST",
      credentials: "include",
      headers: { "content-type": "application/json" },
      body: JSON.stringify(v),
    }, 8000);
    if (!res.ok) throw new Error(`create household: HTTP ${res.status}`);
    const data = await res.json();
    // Deliberately NOT flipping `authenticated` here: the Welcome screen must stay mounted to show
    // the recovery key. Its "Continue" button reloads the page, and the /me probe then adopts the
    // cookie the create response just set.
    return data as { household_id: string; recovery_key: string };
  }, [base]);

  const recoverHousehold = useCallback(async (recovery_key: string) => {
    const res = await fetchWithTimeout(`${base.http}/auth/recover`, {
      method: "POST",
      credentials: "include",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ recovery_key }),
    }, 8000);
    if (!res.ok) throw new Error("That recovery key did not work");
    setAuthenticated(true);
  }, [base]);

  const logoutHousehold = useCallback(async () => {
    await fetchWithTimeout(`${base.http}/auth/logout`, { method: "POST", credentials: "include" }, 5000).catch(() => undefined);
    setAuthenticated(false);
    clearStoredToken();
  }, [base]);

  const createPairing = useCallback(async (kind: "station" | "viewer", opts: { mode_ceiling?: string; label?: string } = {}) => {
    const tok = token ?? getStoredToken();
    const headers: Record<string, string> = { "content-type": "application/json" };
    if (tok) headers["Authorization"] = `Bearer ${tok}`;
    const url = `${base.http}/households/me/pairing${tok ? `?token=${encodeURIComponent(tok)}` : ""}`;
    const res = await fetchWithTimeout(url, { method: "POST", headers, credentials: "include", body: JSON.stringify({ kind, ...opts }) }, 10000);
    if (!res.ok) throw new Error(`pairing: HTTP ${res.status}`);
    return res.json() as Promise<{ code: string; expires_in: number; qr: { v: 2; url: string; pair_url: string; code: string } }>;
  }, [base, token]);

  const joinHousehold = useCallback(async (code: string, label: string) => {
    const res = await fetchWithTimeout(`${base.http}/join`, {
      method: "POST",
      credentials: "include",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ code, label }),
    }, 10000);
    if (!res.ok) throw new Error("That invite code did not work");
    setAuthenticated(true);
  }, [base]);

  const fetchMembers = useCallback(async () => {
    const tok = token ?? getStoredToken();
    const headers: Record<string, string> = {};
    if (tok) headers["Authorization"] = `Bearer ${tok}`;
    const res = await fetchWithTimeout(`${base.http}/households/me/members`, { credentials: "include", headers }, 8000);
    if (!res.ok) throw new Error(`members: HTTP ${res.status}`);
    return res.json() as Promise<{ stations: { id: string; name: string; last_seen: number | null; revoked: boolean }[];
                                   viewers: { id: string; label: string; role: string; mode_ceiling: string; last_seen: number | null; revoked: boolean }[] }>;
  }, [base, token]);

  const fetchSettings = useCallback(async () => {
    const tok = token ?? getStoredToken();
    const headers: Record<string, string> = {};
    if (tok) headers["Authorization"] = `Bearer ${tok}`;
    const res = await fetchWithTimeout(`${base.http}/households/me/settings`, { credentials: "include", headers }, 8000);
    if (!res.ok) throw new Error(`settings: HTTP ${res.status}`);
    return res.json() as Promise<{ llm_enabled: boolean; log_enabled: boolean }>;
  }, [base, token]);

  const saveSettings = useCallback(async (next: { llm_enabled: boolean; log_enabled: boolean }) => {
    const tok = token ?? getStoredToken();
    const headers: Record<string, string> = { "Content-Type": "application/json" };
    if (tok) headers["Authorization"] = `Bearer ${tok}`;
    const res = await fetchWithTimeout(`${base.http}/households/me/settings`, {
      method: "PATCH", credentials: "include", headers, body: JSON.stringify(next),
    }, 8000);
    if (!res.ok) throw new Error(`save settings: HTTP ${res.status}`);
    return res.json() as Promise<{ llm_enabled: boolean; log_enabled: boolean }>;
  }, [base, token]);

  const revokeViewer = useCallback(async (viewerId: string) => {
    const tok = token ?? getStoredToken();
    const req = revokeViewerRequest(base.http, viewerId, tok);
    const res = await fetchWithTimeout(req.url, req.init, 8000);
    if (!res.ok) throw new Error(`revoke: HTTP ${res.status}`);
  }, [base, token]);

  const revokeStation = useCallback(async (stationId: string) => {
    const tok = token ?? getStoredToken();
    const req = revokeStationRequest(base.http, stationId, tok);
    const res = await fetchWithTimeout(req.url, req.init, 8000);
    if (!res.ok) throw new Error(`revoke station: HTTP ${res.status}`);
    void fetchWithTimeout(authedUrl(base.http, "/status", tok), { headers: authedHeaders(tok), credentials: "include", cache: "no-store" }, 6000)
      .then((r) => r.ok && r.json())
      .then((st: Status) => st && dispatch({ kind: "status", status: st }))
      .catch(() => undefined);
  }, [base, token]);

  const refreshStatus = useCallback(async () => {
    const tok = token ?? getStoredToken();
    const headers: Record<string, string> = {};
    if (tok) headers["Authorization"] = `Bearer ${tok}`;
    try {
      const res = await fetchWithTimeout(`${base.http}/status${tok ? `?token=${encodeURIComponent(tok)}` : ""}`, {
        headers,
        credentials: "include",
        cache: "no-store",
      }, 6000);
      if (res.ok) {
        const st = (await res.json()) as Status;
        dispatch({ kind: "status", status: st });
        return st;
      }
    } catch {
      // ignore
    }
    return undefined;
  }, [base, token]);

  const deleteHousehold = useCallback(async (recovery_key: string) => {
    const res = await fetchWithTimeout(`${base.http}/households/me`, {
      method: "DELETE",
      credentials: "include",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ recovery_key }),
    }, 8000);
    if (!res.ok) throw new Error(`delete household: HTTP ${res.status}`);
    setAuthenticated(false);
  }, [base]);

  const saveProfile = useCallback(async (patch: { dog_name: string; location: string; zone_label: string }) => {
    const tok = token ?? getStoredToken();
    if (authMode === "households") {
      const res = await fetchWithTimeout(`${base.http}/households/me/profile`, {
        method: "PUT",
        credentials: "include",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({ dog_name: patch.dog_name, location: patch.location }),
      }, 6000);
      if (!res.ok) throw new Error(`profile: HTTP ${res.status}`);
      return res.json();
    }
    const headers: Record<string, string> = { "content-type": "application/json" };
    if (tok) headers["Authorization"] = `Bearer ${tok}`;
    const url = `${base.http}/profile${tok ? `?token=${encodeURIComponent(tok)}` : ""}`;
    const res = await fetchWithTimeout(url, { method: "PUT", headers, body: JSON.stringify(patch) }, 6000);
    if (res.status === 401) {
      setAuthRequired(true);
      setAuthenticated(false);
      throw new Error("HTTP 401 Unauthorized");
    }
    if (!res.ok) throw new Error(`profile: HTTP ${res.status}`);
    return res.json();
  }, [base, token, authMode]);

  const setPrivacy = useCallback(async (enabled?: boolean) => {
    const tok = token ?? getStoredToken();
    const headers: Record<string, string> = { "content-type": "application/json" };
    if (tok) headers["Authorization"] = `Bearer ${tok}`;
    const url = `${base.http}/privacy${tok ? `?token=${encodeURIComponent(tok)}` : ""}`;
    const target = enabled !== undefined ? enabled : !privacyMode;
    setPrivacyOverride(target);
    try {
      const res = await fetchWithTimeout(url, {
        method: "POST",
        headers,
        body: JSON.stringify({ enabled: target }),
      }, 5000);
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
  const focusStation = useCallback((stationId: string) => dispatch({ kind: "focusStation", stationId }), []);

  const focusRef = useRef<{ station?: string; view: "grid" | "focus" | "none" }>({ view: "focus" });

  const reportFocus = useCallback(
    async (station?: string, view: "grid" | "focus" | "none" = "focus") => {
      focusRef.current = { station, view };
      const tok = token ?? getStoredToken();
      const headers: Record<string, string> = { "content-type": "application/json" };
      if (tok) headers["Authorization"] = `Bearer ${tok}`;
      const url = `${base.http}/focus${tok ? `?token=${encodeURIComponent(tok)}` : ""}`;
      try {
        await fetchWithTimeout(url, {
          method: "POST",
          headers,
          body: JSON.stringify({ station, view }),
        }, 4000);
      } catch {
        /* ignore */
      }
    },
    [base, token]
  );

  useEffect(() => {
    if (authRequired) return;

    const interval = setInterval(() => {
      if (typeof document !== "undefined" && document.visibilityState === "hidden") {
        return;
      }
      const cur = focusRef.current;
      reportFocus(cur.station, cur.view);
    }, 15000);

    const onVisibilityChange = () => {
      if (typeof document !== "undefined" && document.visibilityState === "hidden") {
        reportFocus(undefined, "none");
      } else {
        const cur = focusRef.current;
        reportFocus(cur.station, cur.view);
      }
    };

    if (typeof document !== "undefined") {
      document.addEventListener("visibilitychange", onVisibilityChange);
    }
    return () => {
      clearInterval(interval);
      if (typeof document !== "undefined") {
        document.removeEventListener("visibilitychange", onVisibilityChange);
      }
    };
  }, [authRequired, reportFocus]);

  return {
    state,
    treat,
    readAll,
    setMode,
    dismissToast,
    focusStation,
    reportFocus,
    http: base.http,
    token,
    authRequired,
    authenticated,
    verifyPin,
    logout,
    privacyMode,
    setPrivacy,
    effectiveMode,
    stationCap,
    householdMode,
    setViewMode,
    peek,
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
    refreshStatus,
  };
}
