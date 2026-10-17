/**
 * Cloudflare Worker: Zero-Downtime High-Availability Failover for WagWatch
 * Domain: watch.furryfolks.co
 *
 * Routing Policy:
 * 1. Primary: Self-Hosted Dell Inspiron (via Cloudflare Tunnel origin)
 * 2. Fallback: Render Cloud Deployment (behind-the-barks.onrender.com)
 *
 * Guaranteed Behavior:
 * - Transparent failover on 502 / 503 / 504 / timeout / network error
 * - Real bridged WebSocket upgrade handling for /ingest-events (Android station phone) and
 *   dashboard sockets: the worker fetches the upgrade from the surviving origin, accepts both
 *   socket ends, and relays messages/close frames 1:1 (event-driven, no re-buffering). The
 *   30 s primary cooldown applies to WebSocket requests exactly as to HTTP ones.
 * - Zero configuration or URL change needed on the Android app
 */

const CONFIG = {
  // Primary origin: Tunnel hostname or direct tunnel address for Dell Inspiron
  PRIMARY_ORIGIN: "https://primary-watch.furryfolks.co",
  // Standby fallback origin: Render service deployment
  FALLBACK_ORIGIN: "https://behind-the-barks.onrender.com",
  // Primary timeout before switching to fallback (ms) — HTTP requests only; never applied to WebSockets
  PRIMARY_TIMEOUT_MS: 3000,
  // Cooldown period after primary failure before retrying primary (ms)
  CIRCUIT_BREAKER_COOLDOWN_MS: 30000,
};

// Status codes that indicate an origin server failure (tunnel down, uvicorn crashed)
const BAD_GATEWAY_STATUSES = [502, 503, 504, 530];

let primaryFailedAt = 0;

export default {
  async fetch(request, env, ctx) {
    const url = new URL(request.url);
    const primaryUrl = new URL(url.pathname + url.search, env.PRIMARY_ORIGIN || CONFIG.PRIMARY_ORIGIN);
    const fallbackUrl = new URL(url.pathname + url.search, env.FALLBACK_ORIGIN || CONFIG.FALLBACK_ORIGIN);

    if (request.headers.get("Upgrade")?.toLowerCase() === "websocket") {
      return requestWebSocket(request, primaryUrl, fallbackUrl, env);
    }
    return requestHttp(request, primaryUrl, fallbackUrl, env);
  },
};

// ---------------------------------------------------------------------------
// WebSocket relay (behind-the-barks.onrender.com). WS upgrades would NOT survive the plain
// HTTP proxy path: status 101 is only constructible in the Workers runtime when paired with
// a `webSocket` property, and long-lived sockets must never sit behind a 5 s abort timer.
// ---------------------------------------------------------------------------

async function requestWebSocket(request, primaryUrl, fallbackUrl, env) {
  const now = Date.now();
  const primaryCoolingDown = (now - primaryFailedAt) < (env.CIRCUIT_BREAKER_COOLDOWN_MS || CONFIG.CIRCUIT_BREAKER_COOLDOWN_MS);

  // If primary is currently known to be down, upgrade straight against the fallback
  if (primaryCoolingDown) {
    return failoverWebSocket(request, fallbackUrl, "circuit_open");
  }

  let upstream;
  try {
    // No AbortController here on purpose: a hard deadline kills healthy long-lived sockets.
    // Handshake/transport failures surface as a thrown fetch; clean closes are relayed below.
    upstream = await fetchUpgraded(primaryUrl, request);
  } catch (err) {
    primaryFailedAt = Date.now();
    return failoverWebSocket(request, fallbackUrl, "network_error");
  }

  if (BAD_GATEWAY_STATUSES.includes(upstream.status)) {
    primaryFailedAt = Date.now();
    return failoverWebSocket(request, fallbackUrl, `http_${upstream.status}`);
  }

  if (!upstream.webSocket) {
    // Primary answered, but not with an upgrade (e.g. an app-level 4xx page). Relay it verbatim:
    // failover exists for dead infrastructure, not for the origin refusing this particular socket.
    const headers = new Headers(upstream.headers);
    headers.set("X-WagWatch-Origin", "dell-inspiron-primary");
    return new Response(upstream.body, { status: upstream.status, statusText: upstream.statusText, headers });
  }

  return bridgeWebSocket(upstream, "dell-inspiron-primary", null);
}

async function failoverWebSocket(request, fallbackUrl, reason) {
  let upstream;
  try {
    upstream = await fetchUpgraded(fallbackUrl, request);
  } catch (err) {
    return wsUnavailable(reason, "network_error");
  }

  if (BAD_GATEWAY_STATUSES.includes(upstream.status)) {
    return wsUnavailable(reason, `upstream_${upstream.status}`);
  }

  if (!upstream.webSocket) {
    const headers = new Headers(upstream.headers);
    headers.set("X-WagWatch-Origin", "render-fallback");
    headers.set("X-WagWatch-Failover-Reason", reason);
    return new Response(upstream.body, { status: upstream.status, statusText: upstream.statusText, headers });
  }

  return bridgeWebSocket(upstream, "render-fallback", reason);
}

// Fetch `url` keeping the upgrade request semantics (the caller's request is a GET upgrade).
function fetchUpgraded(url, request) {
  return fetch(new Request(url.toString(), {
    method: request.method, // a WebSocket upgrade is necessarily GET
    headers: request.headers, // Upgrade / Connection / Sec-WebSocket-* / auth cookies ride along
    redirect: "manual",
  }));
}

// Wire the (already accepted) upstream socket to a fresh pair served to the client, 1:1 and
// event-driven. The Workers runtime holds each socket's events until its handlers accept them,
// so plain forwarding here preserves order without any hand-rolled unbounded queue.
function bridgeWebSocket(upstreamResponse, originLabel, reason) {
  const upstreamSocket = upstreamResponse.webSocket;
  upstreamSocket.accept();

  const pair = new WebSocketPair();
  const relaySide = pair[0]; // worker-side end, wired straight to the origin
  const clientSide = pair[1]; // delivered to the requesting app inside the 101 response
  relaySide.accept();

  relaySide.addEventListener("message", (event) => {
    try { upstreamSocket.send(event.data); } catch { /* origin already gone; close relay handles the rest */ }
  });
  upstreamSocket.addEventListener("message", (event) => {
    try { relaySide.send(event.data); } catch { /* client socket already gone */ }
  });

  // Close frames are forwarded with code + reason so both peers see the real shutdown story.
  relaySide.addEventListener("close", (event) => safeClose(upstreamSocket, event.code, event.reason));
  upstreamSocket.addEventListener("close", (event) => safeClose(relaySide, event.code, event.reason));
  // An error without a close frame: signal an abnormal shutdown (1011) to the other peer.
  relaySide.addEventListener("error", () => safeClose(upstreamSocket, 1011, "relay error"));
  upstreamSocket.addEventListener("error", () => safeClose(relaySide, 1011, "relay error"));

  const headers = new Headers(upstreamResponse.headers);
  headers.set("X-WagWatch-Origin", originLabel);
  if (reason) headers.set("X-WagWatch-Failover-Reason", reason);

  // Status 101 is only constructible alongside a `webSocket` (Workers runtime contract).
  return new Response(null, { status: 101, statusText: "Switching Protocols", webSocket: clientSide });
}

// Both origins unusable for this upgrade: answer with an honest HTTP 502 so the callers' existing
// reconnect logic (EventUploader single-flight, dashboard retry) takes over.
function wsUnavailable(reason, detail) {
  return new Response(
    JSON.stringify({ error: "websocket_failover_failed", reason, detail }) + "\n",
    {
      status: 502,
      headers: {
        "content-type": "application/json",
        "x-wagwatch-origin": "none",
        "x-wagwatch-failover-reason": `${reason}:${detail}`,
      },
    },
  );
}

function safeClose(socket, code, reason) {
  try {
    if (socket.readyState !== socket.READY_STATE_CLOSED) socket.close(code, reason);
  } catch { /* already closing/closed */ }
}

// ---------------------------------------------------------------------------
// Plain HTTP relay (primary / fallback circuit breaker for dashboard & API requests)
// ---------------------------------------------------------------------------

async function requestHttp(request, primaryUrl, fallbackUrl, env) {
  const now = Date.now();
  const primaryCoolingDown = (now - primaryFailedAt) < (env.CIRCUIT_BREAKER_COOLDOWN_MS || CONFIG.CIRCUIT_BREAKER_COOLDOWN_MS);

  // If primary is currently known to be down, route straight to fallback
  if (primaryCoolingDown) {
    return proxyToFallback(request, fallbackUrl, "circuit_open");
  }

  try {
    const controller = new AbortController();
    const timeoutId = setTimeout(() => controller.abort(), env.PRIMARY_TIMEOUT_MS || CONFIG.PRIMARY_TIMEOUT_MS);

    const response = await fetch(new Request(primaryUrl.toString(), {
      method: request.method,
      headers: request.headers,
      body: request.body,
      redirect: "manual",
      signal: controller.signal,
    }));
    clearTimeout(timeoutId);

    // Status codes that indicate primary server failure (tunnel down, uvicorn crashed)
    if (BAD_GATEWAY_STATUSES.includes(response.status)) {
      primaryFailedAt = Date.now();
      return proxyToFallback(request, fallbackUrl, `http_${response.status}`);
    }

    // Success from Dell Inspiron
    const resHeaders = new Headers(response.headers);
    resHeaders.set("X-WagWatch-Origin", "dell-inspiron-primary");
    return new Response(response.body, {
      status: response.status,
      statusText: response.statusText,
      headers: resHeaders,
    });
  } catch (err) {
    primaryFailedAt = Date.now();
    return proxyToFallback(request, fallbackUrl, err.name === "AbortError" ? "timeout" : "network_error");
  }
}

async function proxyToFallback(request, fallbackUrl, reason) {
  const response = await fetch(new Request(fallbackUrl.toString(), {
    method: request.method,
    headers: request.headers,
    body: request.body,
    redirect: "manual",
  }));
  const resHeaders = new Headers(response.headers);
  resHeaders.set("X-WagWatch-Origin", "render-fallback");
  resHeaders.set("X-WagWatch-Failover-Reason", reason);

  return new Response(response.body, {
    status: response.status,
    statusText: response.statusText,
    headers: resHeaders,
  });
}
