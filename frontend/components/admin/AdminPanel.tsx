"use client";

import { useState } from "react";
import { backendBase } from "@/lib/config";
import {
  adminErrorMessage,
  adminHeaders,
  changedTuning,
  isTuning,
  validateTuning,
  type Tuning,
  type TuningKey,
} from "@/lib/admin";

type Draft = { video_fps: string; ws_frame_fps: string; gzip_enabled: boolean; gzip_min_size: string };

function toDraft(t: Tuning): Draft {
  return { video_fps: String(t.video_fps), ws_frame_fps: String(t.ws_frame_fps),
    gzip_enabled: t.gzip_enabled, gzip_min_size: String(t.gzip_min_size) };
}

/** Numbers from the form; anything that is not a number stays NaN so validateTuning flags it. */
function fromDraft(d: Draft): Tuning {
  const num = (s: string) => (s.trim() === "" ? Number.NaN : Number(s));
  return { video_fps: num(d.video_fps), ws_frame_fps: num(d.ws_frame_fps),
    gzip_enabled: d.gzip_enabled, gzip_min_size: num(d.gzip_min_size) };
}

const FIELDS: { key: Exclude<TuningKey, "gzip_enabled">; label: string; hint: string; step: string }[] = [
  { key: "video_fps", label: "Video stream rate (fps)", hint: "Ceiling for the MJPEG stream. Only changed frames are sent. Main video cost.", step: "0.5" },
  { key: "ws_frame_fps", label: "Dashboard preview rate (fps)", hint: "Max preview messages per second per dashboard connection.", step: "0.5" },
  { key: "gzip_min_size", label: "Compress responses over (bytes)", hint: "Smaller responses are sent as-is.", step: "100" },
];

export default function AdminPanel() {
  // The token is typed each visit and kept in memory only: nothing is written to browser storage.
  const [token, setToken] = useState("");
  const [current, setCurrent] = useState<Tuning | null>(null);
  const [draft, setDraft] = useState<Draft | null>(null);
  const [errors, setErrors] = useState<Partial<Record<TuningKey, string>>>({});
  const [message, setMessage] = useState<{ tone: "ok" | "error"; text: string } | null>(null);
  const [busy, setBusy] = useState(false);

  const base = backendBase().http;

  async function load(e?: React.FormEvent) {
    e?.preventDefault();
    setBusy(true);
    setMessage(null);
    try {
      const res = await fetch(`${base}/admin/tuning`, { headers: adminHeaders(token) });
      const body: unknown = await res.json().catch(() => null);
      if (!res.ok) {
        setMessage({ tone: "error", text: adminErrorMessage(res.status, body) });
        return;
      }
      if (!isTuning(body)) {
        setMessage({ tone: "error", text: "The server sent an unexpected reply." });
        return;
      }
      setCurrent(body);
      setDraft(toDraft(body));
      setErrors({});
    } catch {
      setMessage({ tone: "error", text: `Could not reach ${base}.` });
    } finally {
      setBusy(false);
    }
  }

  async function save() {
    if (!current || !draft) return;
    const next = fromDraft(draft);
    const problems = validateTuning(next);
    setErrors(problems);
    if (Object.keys(problems).length > 0) return;
    const patch = changedTuning(current, next);
    if (Object.keys(patch).length === 0) {
      setMessage({ tone: "ok", text: "Nothing changed." });
      return;
    }
    setBusy(true);
    try {
      const res = await fetch(`${base}/admin/tuning`, {
        method: "PATCH", headers: adminHeaders(token), body: JSON.stringify(patch),
      });
      const body: unknown = await res.json().catch(() => null);
      if (!res.ok) {
        setMessage({ tone: "error", text: adminErrorMessage(res.status, body) });
        return;
      }
      if (isTuning(body)) {
        setCurrent(body);
        setDraft(toDraft(body));
      }
      setMessage({ tone: "ok", text: "Saved. Applies to new frames and responses right away." });
    } catch {
      setMessage({ tone: "error", text: `Could not reach ${base}.` });
    } finally {
      setBusy(false);
    }
  }

  const inputClass = "h-11 w-full rounded-xl border border-border bg-surface-2 px-3 text-[14px] text-text outline-none focus:border-accent";

  return (
    <main className="mx-auto flex max-w-xl flex-col gap-6 px-4 py-8">
      <header>
        <h1 className="text-[22px] font-bold">Bandwidth settings</h1>
        <p className="mt-1 text-[13px] text-muted">
          Changes take effect without a redeploy and are kept across restarts.
        </p>
      </header>

      <form onSubmit={load} className="flex flex-col gap-3 rounded-2xl border border-border bg-surface p-4">
        <label className="flex flex-col gap-1 text-[13px] font-semibold">
          Admin token
          <input type="password" autoComplete="off" value={token} onChange={(e) => setToken(e.target.value)}
            className={inputClass} placeholder="BTB_API_TOKEN" />
        </label>
        <button type="submit" disabled={busy || !token.trim()}
          className="h-11 rounded-xl bg-accent px-4 text-[14px] font-bold text-accent-fg transition-colors hover:bg-accent/90 disabled:opacity-60">
          {current ? "Reload" : "Load settings"}
        </button>
      </form>

      {draft && (
        <section className="flex flex-col gap-5 rounded-2xl border border-border bg-surface p-4">
          {FIELDS.map((f) => (
            <label key={f.key} className="flex flex-col gap-1 text-[13px] font-semibold">
              {f.label}
              <input type="number" inputMode="decimal" step={f.step} value={draft[f.key]}
                onChange={(e) => setDraft({ ...draft, [f.key]: e.target.value })}
                aria-invalid={Boolean(errors[f.key])} className={inputClass} />
              <span className="text-[12px] font-normal text-muted">{f.hint}</span>
              {errors[f.key] && <span role="alert" className="text-[12px] text-red-400">{errors[f.key]}</span>}
            </label>
          ))}
          <label className="flex items-center justify-between gap-3 text-[13px] font-semibold">
            <span>
              Compress text responses (gzip)
              <span className="block text-[12px] font-normal text-muted">Streams and video are never compressed.</span>
            </span>
            <input type="checkbox" checked={draft.gzip_enabled}
              onChange={(e) => setDraft({ ...draft, gzip_enabled: e.target.checked })} />
          </label>
          <button type="button" onClick={() => void save()} disabled={busy}
            className="h-11 rounded-xl bg-accent px-4 text-[14px] font-bold text-accent-fg transition-colors hover:bg-accent/90 disabled:opacity-60">
            {busy ? "Saving…" : "Save changes"}
          </button>
        </section>
      )}

      {message && (
        <p role="status" className={message.tone === "ok" ? "text-[13px] text-happy" : "text-[13px] text-red-400"}>
          {message.text}
        </p>
      )}
    </main>
  );
}
