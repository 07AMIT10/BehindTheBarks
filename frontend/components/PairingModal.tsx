"use client";

import { useEffect, useState } from "react";
import QRCode from "qrcode";

type Props = {
  isOpen: boolean;
  onClose: () => void;
  token?: string | null;
  phoneConnected?: boolean;
  deviceName?: string | null;
};

export default function PairingModal({ isOpen, onClose, token, phoneConnected, deviceName }: Props) {
  const [qrDataUrl, setQrDataUrl] = useState<string | null>(null);
  const [copied, setCopied] = useState<boolean>(false);

  const wsUrl = typeof window !== "undefined"
    ? `${window.location.protocol === "https:" ? "wss:" : "ws:"}//${window.location.host}/ingest-events`
    : "";

  useEffect(() => {
    if (!isOpen || !wsUrl) return;

    const pairingPayload = JSON.stringify({
      v: 1,
      url: wsUrl,
      token: token || undefined,
    });

    QRCode.toDataURL(pairingPayload, {
      width: 256,
      margin: 2,
      color: {
        dark: "#000000",
        light: "#ffffff",
      },
      errorCorrectionLevel: "M",
    })
      .then((url) => setQrDataUrl(url))
      .catch((err) => console.error("QR Code generation error:", err));
  }, [isOpen, wsUrl, token]);

  if (!isOpen) return null;

  const handleCopy = () => {
    if (!wsUrl) return;
    navigator.clipboard.writeText(wsUrl);
    setCopied(true);
    setTimeout(() => setCopied(false), 2000);
  };

  return (
    <div
      role="dialog"
      aria-modal="true"
      aria-labelledby="pairing-title"
      className="fixed inset-0 z-50 flex items-center justify-center p-4 bg-black/75 backdrop-blur-md animate-in fade-in duration-200"
    >
      <div className="relative w-full max-w-md rounded-2xl border border-white/10 bg-[#16181d] p-6 shadow-2xl flex flex-col items-center">
        {/* Close Button */}
        <button
          type="button"
          onClick={onClose}
          className="absolute right-4 top-4 text-muted hover:text-white p-1 rounded-lg transition-colors"
          aria-label="Close dialog"
        >
          <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
            <line x1="18" y1="6" x2="6" y2="18" />
            <line x1="6" y1="6" x2="18" y2="18" />
          </svg>
        </button>

        <div className="flex h-12 w-12 items-center justify-center rounded-2xl bg-accent/15 border border-accent/30 text-accent mb-3">
          <svg width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
            <rect x="5" y="2" width="14" height="20" rx="2" ry="2" />
            <line x1="12" y1="18" x2="12.01" y2="18" />
          </svg>
        </div>

        <h2 id="pairing-title" className="text-xl font-bold tracking-tight text-white mb-1">
          Pair Phone Station
        </h2>
        <p className="text-xs text-muted text-center max-w-xs mb-4">
          Point your phone’s Behind The Barks app at this QR code to instantly configure the server stream.
        </p>

        {/* QR Code Container */}
        <div className="relative p-3 bg-white rounded-2xl shadow-inner mb-4 flex items-center justify-center">
          {qrDataUrl ? (
            // eslint-disable-next-line @next/next/no-img-element
            <img src={qrDataUrl} alt="Pairing QR Code" width={220} height={220} className="rounded-lg" />
          ) : (
            <div className="w-[220px] h-[220px] flex items-center justify-center text-xs text-black/50">
              Generating QR Code...
            </div>
          )}
          {phoneConnected && (
            <div className="absolute inset-0 bg-black/80 backdrop-blur-sm rounded-2xl flex flex-col items-center justify-center text-center p-4">
              <span className="text-2xl mb-1">✓</span>
              <div className="text-sm font-semibold text-emerald-400">Phone Connected!</div>
              <div className="text-xs text-muted mt-1">{deviceName ?? "Camera Station Active"}</div>
            </div>
          )}
        </div>

        {/* URL Pill & Copy */}
        <div className="w-full flex items-center gap-2 p-2 rounded-xl bg-white/5 border border-white/10 text-xs text-muted font-mono mb-4">
          <span className="truncate flex-1 pl-1 text-[11px] text-gray-300">{wsUrl}</span>
          <button
            type="button"
            onClick={handleCopy}
            className="px-2.5 py-1 rounded-lg bg-accent/20 hover:bg-accent/30 text-accent font-sans text-xs font-medium transition-colors"
          >
            {copied ? "Copied!" : "Copy"}
          </button>
        </div>

        {/* Instructions */}
        <div className="w-full text-left space-y-2 text-xs text-muted bg-white/[0.02] p-3 rounded-xl border border-white/5 mb-2">
          <div className="flex items-start gap-2">
            <span className="flex h-4 w-4 shrink-0 items-center justify-center rounded-full bg-accent/20 text-[10px] text-accent font-bold">1</span>
            <span>Open Behind The Barks APK on your Android device.</span>
          </div>
          <div className="flex items-start gap-2">
            <span className="flex h-4 w-4 shrink-0 items-center justify-center rounded-full bg-accent/20 text-[10px] text-accent font-bold">2</span>
            <span>Tap <strong>📷 Scan QR</strong> and aim at this screen.</span>
          </div>
          <div className="flex items-start gap-2">
            <span className="flex h-4 w-4 shrink-0 items-center justify-center rounded-full bg-accent/20 text-[10px] text-accent font-bold">3</span>
            <span>Tap <strong>Start Monitoring</strong>, then tap <strong>🌙 Station Dim</strong>.</span>
          </div>
        </div>

        <button
          type="button"
          onClick={onClose}
          className="w-full mt-2 py-2 rounded-xl bg-white/10 hover:bg-white/15 text-white text-xs font-semibold transition-colors"
        >
          Done
        </button>
      </div>
    </div>
  );
}
