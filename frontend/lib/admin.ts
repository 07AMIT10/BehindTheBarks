// Admin tuning (live bandwidth knobs): pure helpers for the /admin panel. The server is the authority:
// these bounds mirror backend/web/tuning.py, so a bad value is caught before the round trip and the
// server still validates every PATCH.

export type Tuning = {
  video_fps: number;
  ws_frame_fps: number;
  gzip_enabled: boolean;
  gzip_min_size: number;
};

export type TuningKey = keyof Tuning;

/** Mirrors TUNABLE in backend/web/tuning.py. */
export const TUNING_LIMITS: Record<"video_fps" | "ws_frame_fps" | "gzip_min_size", { min: number; max: number }> = {
  video_fps: { min: 0.5, max: 30 },
  ws_frame_fps: { min: 0.5, max: 30 },
  gzip_min_size: { min: 0, max: 1_000_000 },
};

/** Field errors keyed by TuningKey. An empty object means the draft can be sent. */
export function validateTuning(draft: Partial<Record<TuningKey, unknown>>): Partial<Record<TuningKey, string>> {
  const errors: Partial<Record<TuningKey, string>> = {};
  for (const key of ["video_fps", "ws_frame_fps", "gzip_min_size"] as const) {
    if (!(key in draft)) continue;
    const raw = draft[key];
    const { min, max } = TUNING_LIMITS[key];
    if (typeof raw !== "number" || !Number.isFinite(raw)) {
      errors[key] = "Enter a number";
    } else if (raw < min || raw > max) {
      errors[key] = `Must be between ${min} and ${max}`;
    }
  }
  if ("gzip_enabled" in draft && typeof draft.gzip_enabled !== "boolean") {
    errors.gzip_enabled = "Must be on or off";
  }
  return errors;
}

/** The smallest PATCH body: only the keys the operator actually changed. */
export function changedTuning(current: Tuning, draft: Tuning): Partial<Tuning> {
  const patch: Partial<Tuning> = {};
  if (draft.video_fps !== current.video_fps) patch.video_fps = draft.video_fps;
  if (draft.ws_frame_fps !== current.ws_frame_fps) patch.ws_frame_fps = draft.ws_frame_fps;
  if (draft.gzip_enabled !== current.gzip_enabled) patch.gzip_enabled = draft.gzip_enabled;
  if (draft.gzip_min_size !== current.gzip_min_size) patch.gzip_min_size = draft.gzip_min_size;
  return patch;
}

/** Turns a server error body into one line for the operator. */
export function adminErrorMessage(status: number, body: unknown): string {
  const detail = body && typeof body === "object" && "detail" in body ? String((body as { detail: unknown }).detail) : "";
  if (status === 401) return "The admin token was not accepted.";
  if (status === 403) return detail || "The admin API is disabled on this server (no BTB_API_TOKEN set).";
  if (status === 400) return detail || "The server rejected those values.";
  return detail || `Request failed (HTTP ${status}).`;
}

/** Whether a parsed GET response has the shape the panel needs. */
export function isTuning(value: unknown): value is Tuning {
  if (!value || typeof value !== "object") return false;
  const v = value as Record<string, unknown>;
  return typeof v.video_fps === "number" && typeof v.ws_frame_fps === "number"
    && typeof v.gzip_enabled === "boolean" && typeof v.gzip_min_size === "number";
}

/** Authorization header for the operator token. Empty token means no header (the server answers 401). */
export function adminHeaders(token: string): Record<string, string> {
  const t = token.trim();
  return t ? { Authorization: `Bearer ${t}`, "Content-Type": "application/json" } : { "Content-Type": "application/json" };
}
