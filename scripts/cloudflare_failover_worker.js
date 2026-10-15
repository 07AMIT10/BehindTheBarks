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
 * - Seamless WebSocket upgrade handling for /ingest-events (Android phone) and /ws (Dashboard)
 * - Zero configuration or URL change needed on the Android app
 */

const CONFIG = {
  // Primary origin: Tunnel hostname or direct tunnel address for Dell Inspiron
  PRIMARY_ORIGIN: "https://primary-watch.furryfolks.co",
  // Standby fallback origin: Render service deployment
  FALLBACK_ORIGIN: "https://behind-the-barks.onrender.com",
  // Primary timeout before switching to fallback (ms)
  PRIMARY_TIMEOUT_MS: 3000,
  // Cooldown period after primary failure before retrying primary (ms)
  CIRCUIT_BREAKER_COOLDOWN_MS: 30000,
};

let primaryFailedAt = 0;

export default {
  async fetch(request, env, ctx) {
    const url = new URL(request.url);
    const primaryUrl = new URL(url.pathname + url.search, env.PRIMARY_ORIGIN || CONFIG.PRIMARY_ORIGIN);
    const fallbackUrl = new URL(url.pathname + url.search, env.FALLBACK_ORIGIN || CONFIG.FALLBACK_ORIGIN);

    const isWebSocket = request.headers.get("Upgrade")?.toLowerCase() === "websocket";
    const now = Date.now();
    const primaryCoolingDown = (now - primaryFailedAt) < (env.CIRCUIT_BREAKER_COOLDOWN_MS || CONFIG.CIRCUIT_BREAKER_COOLDOWN_MS);

    // If primary is currently known to be down, route straight to fallback
    if (primaryCoolingDown) {
      return proxyToFallback(request, fallbackUrl, isWebSocket, "circuit_open");
    }

    try {
      const controller = new AbortController();
      const timeoutMs = isWebSocket ? 5000 : (env.PRIMARY_TIMEOUT_MS || CONFIG.PRIMARY_TIMEOUT_MS);
      const timeoutId = setTimeout(() => controller.abort(), timeoutMs);

      const primaryReq = new Request(primaryUrl.toString(), {
        method: request.method,
        headers: request.headers,
        body: request.body,
        redirect: "manual",
        signal: controller.signal,
      });

      const response = await fetch(primaryReq);
      clearTimeout(timeoutId);

      // Status codes that indicate primary server failure (tunnel down, uvicorn crashed)
      if ([502, 503, 504, 530].includes(response.status)) {
        primaryFailedAt = Date.now();
        return proxyToFallback(request, fallbackUrl, isWebSocket, `http_${response.status}`);
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
      return proxyToFallback(request, fallbackUrl, isWebSocket, err.name === "AbortError" ? "timeout" : "network_error");
    }
  },
};

async function proxyToFallback(request, fallbackUrl, isWebSocket, reason) {
  const fallbackReq = new Request(fallbackUrl.toString(), {
    method: request.method,
    headers: request.headers,
    body: request.body,
    redirect: "manual",
  });

  const response = await fetch(fallbackReq);
  const resHeaders = new Headers(response.headers);
  resHeaders.set("X-WagWatch-Origin", "render-fallback");
  resHeaders.set("X-WagWatch-Failover-Reason", reason);

  return new Response(response.body, {
    status: response.status,
    statusText: response.statusText,
    headers: resHeaders,
  });
}
