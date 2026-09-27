export type BackendBase = { http: string; ws: string };

type Opts = { search?: string; env?: string; protocol?: string; hostname?: string };

function normalise(raw: string): BackendBase {
  const trimmed = raw.trim().replace(/\/+$/, "");
  const http = trimmed.replace(/^ws:/, "http:").replace(/^wss:/, "https:");
  const ws = http.replace(/^http:/, "ws:").replace(/^https:/, "wss:");
  return { http, ws };
}

export function backendBase(opts: Opts = {}): BackendBase {
  const loc = typeof window !== "undefined" ? window.location : undefined;
  const search = opts.search ?? loc?.search ?? "";
  const fromQuery = new URLSearchParams(search).get("backend");
  if (fromQuery) return normalise(fromQuery);
  const env = opts.env ?? process.env.NEXT_PUBLIC_BACKEND_URL ?? "";
  if (env) return normalise(env);
  const protocol = opts.protocol ?? loc?.protocol ?? "http:";
  const hostname = opts.hostname ?? loc?.hostname ?? "localhost";
  return normalise(`${protocol}//${hostname}:8000`);
}
