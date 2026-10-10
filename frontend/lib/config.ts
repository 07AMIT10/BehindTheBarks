export type BackendBase = { http: string; ws: string };

type Opts = {
  search?: string;
  env?: string;
  protocol?: string;
  hostname?: string;
  /** Injected in tests: the page's own location (window.location in the browser). */
  location?: { origin: string; port: string; protocol: string; hostname: string; search: string };
};

function normalise(raw: string): BackendBase {
  const trimmed = raw.trim().replace(/\/+$/, "");
  const http = trimmed.replace(/^ws:/, "http:").replace(/^wss:/, "https:");
  const ws = http.replace(/^http:/, "ws:").replace(/^https:/, "wss:");
  return { http, ws };
}

export function backendBase(opts: Opts = {}): BackendBase {
  const loc = opts.location ?? (typeof window !== "undefined" ? window.location : undefined);
  const search = opts.search ?? loc?.search ?? "";
  const fromQuery = new URLSearchParams(search).get("backend");
  if (fromQuery) return normalise(fromQuery);
  const env = opts.env ?? process.env.NEXT_PUBLIC_BACKEND_URL ?? "";
  if (env) return normalise(env);
  const protocol = opts.protocol ?? loc?.protocol ?? "http:";
  const hostname = opts.hostname ?? loc?.hostname ?? "localhost";
  const nextDevPort = "3000"; // `next dev` serves the UI here; the backend then lives on :8000
  if (protocol === "https:") {
    return normalise(loc?.origin ?? `https://${hostname}`);
  }
  // Anything else on http is the backend serving the built dashboard itself (or a phone on Wi-Fi):
  // the page's own origin is the backend. The v2 invite links and the households cookies only work
  // same-origin, so guessing :8000 for a page served from another port breaks them.
  if (loc && loc.port && loc.port !== nextDevPort) {
    return normalise(loc.origin);
  }
  return normalise(`${protocol}//${hostname}:8000`);
}
