"use client";

import { useState, useRef } from "react";
import { useDialog } from "@/lib/useDialog";

type Props = {
  isOpen: boolean;
  onClose: () => void;
  onDelete: (recovery_key: string) => Promise<void>;
};

/** Owner-only data erasure (T2.3): typed recovery key, then DELETE /households/me. */
export default function DeleteHouseholdModal({ isOpen, onClose, onDelete }: Props) {
  const [key, setKey] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const dialogRef = useRef<HTMLDivElement>(null);
  useDialog(dialogRef, isOpen, onClose);
  if (!isOpen) return null;

  const submit = async () => {
    setBusy(true);
    setError(null);
    try {
      await onDelete(key.trim());
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not delete the household");
      setBusy(false);
    }
  };

  return (
    <div role="dialog" ref={dialogRef} tabIndex={-1} aria-modal="true" aria-labelledby="delete-household-title"
      className="fixed inset-0 z-50 flex items-center justify-center overflow-y-auto p-4 bg-black/75 backdrop-blur-md">
      <div className="w-full max-w-md rounded-2xl border border-white/10 dark m-auto bg-[#16181d] p-6">
        <h2 id="delete-household-title" className="text-xl font-bold text-white">Delete this household</h2>
        <p className="mt-1 text-xs text-muted">
          This erases the household, its viewers and stations, and its event logs on the server. Type your
          recovery key to confirm. This cannot be undone.
        </p>
        <input value={key} onChange={(e) => setKey(e.target.value)} placeholder="Recovery key"
          className="mt-4 h-11 w-full rounded-xl border border-white/10 bg-white/5 px-3 text-[14px] text-white outline-none focus:border-red-500/60" />
        {error && <div role="alert" className="mt-3 rounded-lg bg-red-500/10 px-3 py-2 text-[13px] text-red-300">{error}</div>}
        <div className="mt-5 flex gap-2">
          <button type="button" disabled={busy || key.trim().length === 0} onClick={() => void submit()}
            className="h-11 flex-1 rounded-xl bg-red-600 px-4 text-[14px] font-bold text-white disabled:opacity-50">
            {busy ? "Deleting…" : "Delete household"}
          </button>
          <button type="button" onClick={onClose}
            className="h-11 rounded-xl bg-white/10 px-4 text-[14px] font-semibold text-white">Cancel</button>
        </div>
      </div>
    </div>
  );
}
