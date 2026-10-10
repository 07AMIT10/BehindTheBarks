"use client";

import { useEffect, useState } from "react";

import { toggleSetting } from "../lib/household";

export type Member = {
  stations: { id: string; name: string; last_seen: number | null; revoked: boolean }[];
  viewers: { id: string; label: string; role: string; mode_ceiling: string; last_seen: number | null; revoked: boolean }[];
};

type Props = {
  isOpen: boolean;
  onClose: () => void;
  isOwner: boolean;
  fetchMembers: () => Promise<Member>;
  revokeViewer: (id: string) => Promise<void>;
  revokeStation: (id: string) => Promise<void>;
  fetchSettings?: () => Promise<Settings>;
  saveSettings?: (next: Settings) => Promise<unknown>;
};

/** T2.9: a household's opt-in flags. Both default to off, so nothing leaves the host by surprise. */
export type Settings = { llm_enabled: boolean; log_enabled: boolean };


/** Who can see this household (T2.6): stations and viewers, with owner-only revoke. */
export default function MembersModal({ isOpen, onClose, isOwner, fetchMembers, revokeViewer, revokeStation, fetchSettings, saveSettings }: Props) {
  const [members, setMembers] = useState<Member | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [settings, setSettings] = useState<Settings | null>(null);

  useEffect(() => {
    if (!isOpen) return;
    void fetchMembers().then(setMembers).catch((err) => setError(String(err)));
    void fetchSettings?.().then(setSettings).catch(() => setSettings(null));
  }, [isOpen, fetchMembers, fetchSettings]);

  const toggle = async (key: keyof Settings) => {
    const next = toggleSetting(settings, key);
    if (!next || !settings) return;
    setSettings(next);
    try {
      await saveSettings?.(next);
    } catch (err) {
      setSettings(settings); // revert on failure so the toggle never lies
      setError(err instanceof Error ? err.message : "could not save");
    }
  };

  if (!isOpen) return null;

  const ago = (ts: number | null) => (ts ? new Date(ts * 1000).toLocaleString() : "never");

  const revoke = async (kind: "viewer" | "station", id: string) => {
    setBusy(true);
    setError(null);
    try {
      if (kind === "viewer") await revokeViewer(id);
      else await revokeStation(id);
      setMembers(await fetchMembers());
    } catch (err) {
      setError(err instanceof Error ? err.message : "could not revoke");
    } finally {
      setBusy(false);
    }
  };

  return (
    <div role="dialog" aria-modal="true" aria-labelledby="members-title"
      className="fixed inset-0 z-50 flex items-center justify-center p-4 bg-black/75 backdrop-blur-md">
      <div className="w-full max-w-md rounded-2xl border border-white/10 bg-[#16181d] p-6">
        <div className="mb-4 flex items-center justify-between">
          <h2 id="members-title" className="text-xl font-bold text-white">Household members</h2>
          <button type="button" onClick={onClose} aria-label="Close dialog"
            className="p-1 rounded-lg text-muted hover:text-white transition-colors">
            <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><line x1="18" y1="6" x2="6" y2="18" /><line x1="6" y1="6" x2="18" y2="18" /></svg>
          </button>
        </div>

        {error && <div role="alert" className="mb-3 rounded-lg bg-red-500/10 px-3 py-2 text-[13px] text-red-300">{error}</div>}
        {!members && !error && <div className="text-small text-muted">Loading…</div>}

        {members && (
          <div className="flex flex-col gap-4">
            <section>
              <h3 className="mb-2 text-[13px] font-semibold uppercase tracking-wide text-muted">Stations</h3>
              {members.stations.length === 0 && <p className="text-[13px] text-muted">No phone stations yet.</p>}
              {members.stations.map((s) => (
                <div key={s.id} className="flex items-center justify-between gap-2 py-1.5 text-[14px] text-text">
                  <span className="truncate">{s.name}{s.revoked && <span className="ml-2 text-[12px] text-red-300">revoked</span>}</span>
                  <span className="shrink-0 text-[12px] text-muted">seen {ago(s.last_seen)}</span>
                  {isOwner && !s.revoked && (
                    <button type="button" disabled={busy} onClick={() => void revoke("station", s.id)}
                      className="shrink-0 h-8 rounded-lg bg-red-600/20 px-3 text-[12px] font-semibold text-red-200">Unpair</button>
                  )}
                </div>
              ))}
            </section>
            <section>
              <h3 className="mb-2 text-[13px] font-semibold uppercase tracking-wide text-muted">Viewers</h3>
              {members.viewers.map((v) => (
                <div key={v.id} className="flex items-center justify-between gap-2 py-1.5 text-[14px] text-text">
                  <span className="truncate">
                    {v.label || v.role}
                    <span className="ml-2 text-[12px] text-muted">{v.role} · {v.mode_ceiling}</span>
                    {v.revoked && <span className="ml-2 text-[12px] text-red-300">revoked</span>}
                  </span>
                  {isOwner && v.role !== "owner" && !v.revoked && (
                    <button type="button" disabled={busy} onClick={() => void revoke("viewer", v.id)}
                      className="shrink-0 h-8 rounded-lg bg-red-600/20 px-3 text-[12px] font-semibold text-red-200">Remove</button>
                  )}
                </div>
              ))}
            </section>
            {isOwner && settings && (
              <section>
                <h3 className="mb-2 text-[13px] font-semibold uppercase tracking-wide text-muted">Privacy</h3>
                <p className="mb-3 text-[13px] text-muted">
                  Both are off unless you turn them on. Your phone&rsquo;s video never leaves this host on its own.
                </p>
                <label className="flex items-center justify-between gap-3 py-1.5 text-sm text-white">
                  <span>Use AI interpretation for this household</span>
                  <input type="checkbox" checked={settings.llm_enabled} onChange={() => toggle("llm_enabled")}
                         aria-label="Use AI interpretation" />
                </label>
                <label className="flex items-center justify-between gap-3 py-1.5 text-sm text-white">
                  <span>Save session logs in this household&rsquo;s folder</span>
                  <input type="checkbox" checked={settings.log_enabled} onChange={() => toggle("log_enabled")}
                         aria-label="Save session logs" />
                </label>
              </section>
            )}
          </div>
        )}
      </div>
    </div>
  );
}
