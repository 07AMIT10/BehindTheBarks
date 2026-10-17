"use client";

import { useState, useRef } from "react";
import { useDialog } from "@/lib/useDialog";
import type { Status } from "@/lib/types";

type Props = {
  isOpen: boolean;
  onClose: () => void;
  profile: Status["profile"];
  onSave: (patch: { dog_name: string; location: string; zone_label: string }) => Promise<void>;
};

const FIELDS = [
  { key: "dog_name", label: "Pet name", placeholder: "Your dog", max: 64 },
  { key: "location", label: "Location", placeholder: "Living room (leave empty to hide)", max: 64 },
  { key: "zone_label", label: "Zone label", placeholder: "feeding area", max: 40 },
] as const;

const initial = (profile: Status["profile"]) => ({
  dog_name: profile?.dog_name ?? "",
  location: profile?.location ?? "",
  zone_label: profile?.zone_label ?? "",
});

/**
 * Edit the pet profile every viewer sees (T1.8). One pet per station; Phase 2 makes it per household.
 * The parent mounts this with a fresh key each time it opens, so the form starts from the live values
 * without an effect (a live broadcast from another viewer mid-edit must not reset the form).
 */
export default function ProfileModal({ isOpen, onClose, profile, onSave }: Props) {
  const [form, setForm] = useState(() => initial(profile));
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const dialogRef = useRef<HTMLDivElement>(null);
  useDialog(dialogRef, isOpen, onClose);
  if (!isOpen) return null;

  const submit = async () => {
    setSaving(true);
    setError(null);
    try {
      await onSave({
        dog_name: form.dog_name.trim(),
        location: form.location.trim(),
        zone_label: form.zone_label.trim(),
      });
      onClose();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not save the profile");
    } finally {
      setSaving(false);
    }
  };

  return (
    <div role="dialog" ref={dialogRef} tabIndex={-1} aria-modal="true" aria-labelledby="profile-title"
      className="fixed inset-0 z-50 flex items-center justify-center overflow-y-auto p-4 bg-black/75 backdrop-blur-md">
      <div className="relative w-full max-w-md rounded-2xl border border-white/10 dark m-auto bg-[#16181d] p-6 shadow-2xl">
        <button type="button" onClick={onClose} aria-label="Close dialog"
          className="absolute right-3 top-3 flex h-11 w-11 items-center justify-center rounded-lg text-muted hover:text-white transition-colors md:right-4 md:top-4 md:h-8 md:w-8">
          <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
            <line x1="18" y1="6" x2="6" y2="18" /><line x1="6" y1="6" x2="18" y2="18" />
          </svg>
        </button>

        <h2 id="profile-title" className="text-xl font-bold tracking-tight text-white mb-1">Pet profile</h2>
        <p className="mb-4 text-xs text-muted">Shown in the header, readings and notifications. Every viewer sees the change.</p>

        <div className="flex flex-col gap-3">
          {FIELDS.map((f) => (
            <label key={f.key} className="flex flex-col gap-1 text-[13px] font-semibold text-text-soft">
              {f.label}
              <input
                value={form[f.key]}
                maxLength={f.max + 8}
                onChange={(e) => setForm({ ...form, [f.key]: e.target.value })}
                placeholder={f.placeholder}
                className="h-11 rounded-xl border border-white/10 bg-white/5 px-3 text-[14px] font-normal text-white outline-none focus:border-accent/60"
              />
            </label>
          ))}
        </div>

        {error && <div role="alert" className="mt-3 rounded-lg bg-red-500/10 px-3 py-2 text-[13px] text-red-300">{error}</div>}

        <div className="mt-5 flex gap-2">
          <button type="button" onClick={() => void submit()} disabled={saving}
            className="h-11 flex-1 rounded-xl bg-accent px-4 text-[14px] font-bold text-accent-fg transition-colors hover:bg-accent/90 active:scale-95 disabled:opacity-60">
            {saving ? "Saving…" : "Save"}
          </button>
          <button type="button" onClick={onClose}
            className="h-11 rounded-xl bg-white/10 px-4 text-[14px] font-semibold text-white transition-colors hover:bg-white/15 active:scale-95">
            Cancel
          </button>
        </div>
      </div>
    </div>
  );
}
