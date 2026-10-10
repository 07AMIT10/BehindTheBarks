"use client";

import { useState } from "react";
import { canLeaveRecoveryStep, validateCreateHousehold, validateRecoveryKey } from "@/lib/household";

type Props = {
  onCreate: (v: { dog_name: string; location: string }) => Promise<{ household_id: string; recovery_key: string }>;
  onRecover: (recovery_key: string) => Promise<void>;
};

/** Households-mode front door (T2.3): create a household (key shown once) or recover one. */
export default function WelcomeScreen({ onCreate, onRecover }: Props) {
  const [tab, setTab] = useState<"create" | "recover">("create");
  const [dogName, setDogName] = useState("Your dog");
  const [location, setLocation] = useState("");
  const [key, setKey] = useState("");
  const [recoveryKey, setRecoveryKey] = useState<string | null>(null);
  const [savedIt, setSavedIt] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [copied, setCopied] = useState(false);

  const submit = async () => {
    setError(null);
    const errs = tab === "create" ? validateCreateHousehold({ dog_name: dogName, location }) : validateRecoveryKey(key);
    const first = Object.values(errs)[0];
    if (first) {
      setError(first);
      return;
    }
    setBusy(true);
    try {
      if (tab === "create") {
        const made = await onCreate({ dog_name: dogName.trim() || "Your dog", location: location.trim() });
        setRecoveryKey(made.recovery_key);
      } else {
        await onRecover(key.trim());
      }
    } catch (err) {
      setError(err instanceof Error ? err.message : "Something went wrong");
    } finally {
      setBusy(false);
    }
  };

  const downloadKey = () => {
    if (!recoveryKey) return;
    const blob = new Blob([recoveryKey], { type: "text/plain" });
    const a = document.createElement("a");
    a.href = URL.createObjectURL(blob);
    a.download = "wagwatch-recovery-key.txt";
    a.click();
    URL.revokeObjectURL(a.href);
  };

  if (recoveryKey) {
    return (
      <main className="mx-auto flex min-h-screen w-full max-w-lg flex-col justify-center gap-4 p-6">
        <div className="rounded-2xl border border-border bg-surface p-6">
          <h1 className="text-xl font-bold text-text">Save your recovery key</h1>
          <p className="mt-1 text-small text-muted">
            It is shown once. With it you can get back into this household from any browser; without it,
            losing the session cookie means creating a new household.
          </p>
          <pre className="mt-4 select-all break-all rounded-xl bg-surface-2 p-4 text-[13px] text-text">{recoveryKey}</pre>
          <div className="mt-3 flex gap-2">
            <button type="button" onClick={() => { void navigator.clipboard.writeText(recoveryKey); setCopied(true); }}
              className="h-11 rounded-xl bg-white/10 px-4 text-[14px] font-semibold text-white">
              {copied ? "Copied!" : "Copy"}
            </button>
            <button type="button" onClick={downloadKey}
              className="h-11 rounded-xl bg-white/10 px-4 text-[14px] font-semibold text-white">Download</button>
          </div>
          <label className="mt-4 flex items-center gap-3 text-[14px] text-text">
            <input type="checkbox" checked={savedIt} onChange={(e) => setSavedIt(e.target.checked)} className="h-5 w-5" />
            I saved my recovery key
          </label>
          <button type="button" disabled={!canLeaveRecoveryStep(savedIt)} onClick={() => window.location.reload()}
            className="mt-4 h-11 w-full rounded-xl bg-accent text-[14px] font-bold text-accent-fg disabled:opacity-50">
            Continue to the dashboard
          </button>
        </div>
      </main>
    );
  }

  return (
    <main className="mx-auto flex min-h-screen w-full max-w-lg flex-col justify-center gap-4 p-6">
      <div className="text-center">
        <h1 className="text-2xl font-bold text-text">WagWatch</h1>
        <p className="text-small text-muted">Set up your household to start watching your dog.</p>
      </div>
      <div className="flex gap-2" role="tablist">
        {(["create", "recover"] as const).map((t) => (
          <button key={t} type="button" role="tab" aria-selected={tab === t} onClick={() => setTab(t)}
            className={`h-11 flex-1 rounded-xl text-[14px] font-semibold ${tab === t ? "bg-accent text-accent-fg" : "bg-white/10 text-white"}`}>
            {t === "create" ? "Create household" : "I have a recovery key"}
          </button>
        ))}
      </div>
      <div className="flex flex-col gap-3 rounded-2xl border border-border bg-surface p-6">
        {tab === "create" ? (
          <>
            <label className="flex flex-col gap-1 text-[13px] font-semibold text-text-soft">
              Pet name
              <input value={dogName} onChange={(e) => setDogName(e.target.value)} maxLength={56}
                className="h-11 rounded-xl border border-white/10 bg-white/5 px-3 text-[14px] text-white outline-none focus:border-accent/60" />
            </label>
            <label className="flex flex-col gap-1 text-[13px] font-semibold text-text-soft">
              Location (optional)
              <input value={location} onChange={(e) => setLocation(e.target.value)} maxLength={56}
                placeholder="Living room"
                className="h-11 rounded-xl border border-white/10 bg-white/5 px-3 text-[14px] text-white outline-none focus:border-accent/60" />
            </label>
          </>
        ) : (
          <label className="flex flex-col gap-1 text-[13px] font-semibold text-text-soft">
            Recovery key
            <textarea value={key} onChange={(e) => setKey(e.target.value)} rows={3}
              className="rounded-xl border border-white/10 bg-white/5 p-3 text-[14px] text-white outline-none focus:border-accent/60" />
          </label>
        )}
        {error && <div role="alert" className="rounded-lg bg-red-500/10 px-3 py-2 text-[13px] text-red-300">{error}</div>}
        <button type="button" disabled={busy} onClick={() => void submit()}
          className="h-11 w-full rounded-xl bg-accent text-[14px] font-bold text-accent-fg disabled:opacity-60">
          {busy ? "Working…" : tab === "create" ? "Create household" : "Recover household"}
        </button>
      </div>
    </main>
  );
}
