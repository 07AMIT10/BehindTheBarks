"use client";

import { useEffect, useState, useRef } from "react";
import { useDialog } from "@/lib/useDialog";
import QRCode from "qrcode";

type Props = {
  isOpen: boolean;
  onClose: () => void;
  phoneConnected?: boolean;
  deviceName?: string | null;
  /** Fetched by the parent when the modal opens (an event handler, never an effect). */
  pairing: Pairing | null;
  error?: string | null;
  onRegenerate: () => void;
  onSwitchKind: (kind: "station" | "viewer") => void;
  kind: "station" | "viewer";
  isPaired?: boolean;
  pairedDeviceName?: string | null;
};

export type Pairing = { code: string; expires_in: number; qr: { v: 2; url: string; pair_url: string; code: string } };

/** Pair a station or invite a viewer (T2.6): one code, one QR, a 5-minute countdown, regenerate. */
export default function PairingModal({
  isOpen,
  onClose,
  phoneConnected,
  deviceName,
  pairing,
  error,
  onRegenerate,
  onSwitchKind,
  kind,
  isPaired,
  pairedDeviceName,
}: Props) {
  const [ceiling, setCeiling] = useState("full");
  const [qrUrl, setQrUrl] = useState<string | null>(null);
  const [copied, setCopied] = useState(false);
  const [left, setLeft] = useState(pairing?.expires_in ?? 0);

  // Auto-close when station pairing is confirmed
  useEffect(() => {
    if (!isPaired) return;
    const timer = setTimeout(() => {
      onClose();
    }, 2800);
    return () => clearTimeout(timer);
  }, [isPaired, onClose]);

  // Built from the server's own base URL (pair_url), not window.location: a viewer opening the link
  // may be on a different host/port than the owner's browser tab (e.g. a ?backend= dev override).
  const joinLink = pairing
    ? `${new URL(pairing.qr.pair_url).origin}/?join=${encodeURIComponent(pairing.code)}`
    : "";

  // The modal is mounted before a code exists, so the countdown must start from each new pairing.
  useEffect(() => {
    if (pairing) setLeft(pairing.expires_in);
  }, [pairing]);

  useEffect(() => {
    if (!pairing) return;
    const id = setInterval(() => setLeft((v) => v - 1), 1000);
    return () => clearInterval(id);
  }, [pairing]);

  useEffect(() => {
    if (!pairing) {
      setQrUrl(null);
      return;
    }
    // A station scans {v:2,url,pair_url,code}; a viewer follows the join link (or scans the same QR).
    const payload = kind === "station" ? JSON.stringify(pairing.qr) : joinLink;
    QRCode.toDataURL(payload, { width: 256, margin: 2, errorCorrectionLevel: "M" })
      .then(setQrUrl)
      .catch(() => setQrUrl(null));
  }, [pairing, kind, joinLink]);

  const dialogRef = useRef<HTMLDivElement>(null);
  useDialog(dialogRef, isOpen, onClose);
  if (!isOpen) return null;

  const copyLink = () => {
    void navigator.clipboard.writeText(joinLink).then(() => setCopied(true));
  };

  const showSuccess = Boolean(isPaired || (kind === "station" && phoneConnected));
  const activeDeviceLabel = pairedDeviceName ?? deviceName ?? "Camera Station Active";

  return (
    <div
      role="dialog" ref={dialogRef} tabIndex={-1}
      aria-modal="true"
      aria-labelledby="pairing-title"
      className="fixed inset-0 z-50 flex items-center justify-center overflow-y-auto p-4 bg-black/75 backdrop-blur-md animate-in fade-in duration-200"
    >
      <div className="relative w-full max-w-md rounded-2xl border border-white/10 dark m-auto bg-[#16181d] p-6 shadow-2xl flex flex-col items-center">
        <button type="button" onClick={onClose} aria-label="Close dialog"
          className="absolute right-3 top-3 flex h-11 w-11 items-center justify-center rounded-lg text-muted hover:text-white transition-colors md:right-4 md:top-4 md:h-8 md:w-8">
          <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
            <line x1="18" y1="6" x2="6" y2="18" /><line x1="6" y1="6" x2="18" y2="18" />
          </svg>
        </button>

        <h2 id="pairing-title" className="text-xl font-bold tracking-tight text-white mb-3">Pair a device</h2>

        <div role="tablist" aria-label="Pairing kind" className="mb-4 flex gap-2">
          {(["station", "viewer"] as const).map((t) => (
            <button key={t} type="button" role="tab" aria-selected={kind === t} onClick={() => onSwitchKind(t)}
              className={`h-11 rounded-xl px-4 text-[13px] font-semibold transition-colors ${kind === t ? "bg-accent text-accent-fg" : "bg-white/10 text-white hover:bg-white/15"}`}>
              {t === "station" ? "Pair a phone" : "Invite a viewer"}
            </button>
          ))}
        </div>

        {kind === "viewer" && (
          <div className="mb-3 flex items-center gap-2 text-[13px] text-muted">
            Viewer can see
            <select value={ceiling} onChange={(e) => setCeiling(e.target.value)}
              className="h-9 rounded-lg border border-white/10 bg-white/5 px-2 text-[13px] text-white">
              <option value="full">everything</option>
              <option value="limited">limited (blurred video, Phase 3)</option>
              <option value="privacy">events only</option>
            </select>
          </div>
        )}

        <div className="relative p-3 bg-white rounded-2xl shadow-inner mb-4 flex items-center justify-center min-w-[244px] min-h-[244px]">
          {qrUrl ? (
            // eslint-disable-next-line @next/next/no-img-element
            <img src={qrUrl} alt="Pairing QR Code" width={220} height={220} className="rounded-lg" />
          ) : error ? (
            <div className="w-[220px] h-[220px] flex flex-col items-center justify-center p-3 text-center">
              <span className="text-2xl text-red-500 mb-2">⚠️</span>
              <div className="text-xs text-red-600 font-semibold mb-2">Failed to load pairing code</div>
              <button
                type="button"
                onClick={onRegenerate}
                className="px-3 py-1.5 rounded-lg bg-black text-white text-xs font-semibold hover:bg-black/80 transition-colors"
              >
                Try again
              </button>
            </div>
          ) : (
            <div className="w-[220px] h-[220px] flex flex-col items-center justify-center text-xs text-black/60 gap-2">
              <div className="h-6 w-6 animate-spin rounded-full border-2 border-emerald-600 border-t-transparent" />
              <span>Generating QR code…</span>
            </div>
          )}

          {showSuccess && (
            <div className="absolute inset-0 bg-black/90 backdrop-blur-sm rounded-2xl flex flex-col items-center justify-center text-center p-4">
              <span className="text-3xl mb-1 text-emerald-400">✓</span>
              <div className="text-base font-semibold text-emerald-400">
                {phoneConnected ? "Phone Connected & Live!" : "Station Paired!"}
              </div>
              <div className="text-xs text-muted mt-1">{activeDeviceLabel}</div>
              <div className="text-xs text-gray-300 mt-2">
                {phoneConnected ? "Live stream active." : "Tap \"Start Monitoring\" on your phone."}
              </div>
              <button
                type="button"
                onClick={onClose}
                className="mt-3.5 h-9 px-4 rounded-lg bg-emerald-500/20 hover:bg-emerald-500/30 text-emerald-300 text-xs font-semibold transition-colors"
              >
                Done
              </button>
            </div>
          )}

          {pairing && left <= 0 && !showSuccess && (
            <div className="absolute inset-0 bg-black/80 rounded-2xl flex flex-col items-center justify-center text-center p-4">
              <div className="text-sm font-semibold text-white">Code expired</div>
              <button type="button" onClick={onRegenerate}
                className="mt-2 h-11 rounded-xl bg-accent px-4 text-[13px] font-bold text-accent-fg">Get a new one</button>
            </div>
          )}
        </div>

        {pairing && (
          <div className="w-full flex items-center gap-2 p-2 rounded-xl bg-white/5 border border-white/10 text-xs text-muted font-mono mb-3">
            <span className="truncate flex-1 pl-1 text-[12px] text-gray-300">
              {kind === "station" ? pairing.qr.pair_url : joinLink}
            </span>
            <span className="shrink-0 text-[12px] text-gray-400">{Math.max(left, 0)}s</span>
            {kind === "viewer" && (
              <button type="button" onClick={copyLink}
                className="shrink-0 px-2.5 py-1 rounded-lg bg-accent/20 hover:bg-accent/30 text-accent font-sans text-xs font-medium transition-colors">
                {copied ? "Copied!" : "Copy link"}
              </button>
            )}
          </div>
        )}

        {error && <div role="alert" className="mb-3 w-full rounded-lg bg-red-500/10 px-3 py-2 text-[13px] text-red-300 text-center">{error}</div>}

        <button type="button" onClick={onRegenerate} disabled={!pairing && !error}
          className="w-full h-11 rounded-xl bg-white/10 hover:bg-white/15 disabled:opacity-50 text-white text-xs font-semibold transition-colors">
          Regenerate code
        </button>
        <p className="mt-3 text-center text-[12px] leading-relaxed text-muted">
          {kind === "station" ? (
            <>
              Open WagWatch on the spare phone, tap <strong>Pair by QR</strong>, and scan this code. Don&apos;t have the app yet?{" "}
              <a href="/download" target="_blank" rel="noopener noreferrer" className="text-emerald-400 font-semibold underline hover:text-emerald-300">
                Download WagWatch APK
              </a>
            </>
          ) : (
            "Send this link to the person you want to invite. Opening it adds them as a viewer of this household only."
          )}
        </p>
      </div>
    </div>
  );
}
