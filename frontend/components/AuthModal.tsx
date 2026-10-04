"use client";

import { useEffect, useRef, useState } from "react";

type Props = {
  isOpen: boolean;
  onVerify: (pin: string) => Promise<{ ok: boolean; error?: string }>;
};

export default function AuthModal({ isOpen, onVerify }: Props) {
  const [pin, setPin] = useState("");
  const [showPin, setShowPin] = useState(false);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const inputRef = useRef<HTMLInputElement>(null);

  useEffect(() => {
    if (isOpen) {
      inputRef.current?.focus();
    }
  }, [isOpen]);

  if (!isOpen) return null;

  const handleSubmit = async (e?: React.FormEvent) => {
    if (e) e.preventDefault();
    if (!pin.trim()) {
      setError("Please enter your PIN or access token");
      return;
    }
    setLoading(true);
    setError(null);
    try {
      const res = await onVerify(pin.trim());
      if (!res.ok) {
        setError(res.error || "Incorrect PIN or access token");
        setPin("");
        inputRef.current?.focus();
      }
    } catch {
      setError("Network error. Please try again.");
    } finally {
      setLoading(false);
    }
  };

  const handleDigit = (d: string) => {
    if (pin.length < 32) {
      const next = pin + d;
      setPin(next);
      setError(null);
    }
  };

  const handleBackspace = () => {
    setPin((prev) => prev.slice(0, -1));
    setError(null);
  };

  const handleClear = () => {
    setPin("");
    setError(null);
  };

  return (
    <div
      role="dialog"
      aria-modal="true"
      aria-labelledby="auth-modal-title"
      aria-describedby="auth-modal-desc"
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/80 p-4 backdrop-blur-md animate-in fade-in duration-200"
    >
      <div className="relative w-full max-w-sm overflow-hidden rounded-2xl border border-border bg-[#181A20] p-6 shadow-2xl text-[#E5E7EB]">
        {/* Header */}
        <div className="flex flex-col items-center text-center">
          <div className="mb-3 flex h-14 w-14 items-center justify-center rounded-2xl bg-cyan-500/10 text-cyan-400 ring-1 ring-cyan-500/30 shadow-inner">
            <svg
              width="28"
              height="28"
              viewBox="0 0 24 24"
              fill="none"
              stroke="currentColor"
              strokeWidth="2"
              strokeLinecap="round"
              strokeLinejoin="round"
              aria-hidden="true"
            >
              <rect x="3" y="11" width="18" height="11" rx="2" ry="2" />
              <path d="M7 11V7a5 5 0 0 1 10 0v4" />
            </svg>
          </div>
          <h2 id="auth-modal-title" className="text-xl font-bold tracking-tight text-white">
            Household Access Required
          </h2>
          <p id="auth-modal-desc" className="mt-1 text-xs text-[#9CA3AF]">
            Enter your household PIN or secret token to stream live telemetry and dog camera.
          </p>
        </div>

        {/* Input Form */}
        <form onSubmit={handleSubmit} className="mt-5 flex flex-col gap-4">
          <div className="relative">
            <input
              ref={inputRef}
              type={showPin ? "text" : "password"}
              value={pin}
              onChange={(e) => {
                setPin(e.target.value);
                setError(null);
              }}
              placeholder="Enter PIN"
              inputMode="numeric"
              autoComplete="current-password"
              disabled={loading}
              className="w-full rounded-xl border border-border/80 bg-[#111317] px-4 py-3 text-center text-lg font-mono tracking-widest text-white placeholder-[#4B5563] shadow-inner outline-none transition focus:border-cyan-400 focus:ring-2 focus:ring-cyan-400/20 disabled:opacity-50"
            />
            <button
              type="button"
              onClick={() => setShowPin(!showPin)}
              title={showPin ? "Hide PIN" : "Show PIN"}
              className="absolute right-3 top-1/2 -translate-y-1/2 p-1 text-[#9CA3AF] hover:text-white transition-colors"
            >
              {showPin ? (
                <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                  <path d="M17.94 17.94A10.07 10.07 0 0 1 12 20c-7 0-11-8-11-8a18.45 18.45 0 0 1 5.06-5.94M9.9 4.24A9.12 9.12 0 0 1 12 4c7 0 11 8 11 8a18.5 18.5 0 0 1-2.16 3.19m-6.72-1.07a3 3 0 1 1-4.24-4.24" />
                  <line x1="1" y1="1" x2="23" y2="23" />
                </svg>
              ) : (
                <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                  <path d="M1 12s4-8 11-8 11 8 11 8-4 8-11 8-11-8-11-8z" />
                  <circle cx="12" cy="12" r="3" />
                </svg>
              )}
            </button>
          </div>

          {error && (
            <div className="flex items-center gap-1.5 rounded-lg bg-red-500/10 px-3 py-2 text-xs text-red-400 border border-red-500/20">
              <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                <circle cx="12" cy="12" r="10" />
                <line x1="12" y1="8" x2="12" y2="12" />
                <line x1="12" y1="16" x2="12.01" y2="16" />
              </svg>
              <span>{error}</span>
            </div>
          )}

          {/* Quick Keypad */}
          <div className="grid grid-cols-3 gap-2">
            {[1, 2, 3, 4, 5, 6, 7, 8, 9].map((n) => (
              <button
                key={n}
                type="button"
                onClick={() => handleDigit(String(n))}
                disabled={loading}
                className="flex h-12 items-center justify-center rounded-xl bg-[#22252C] text-lg font-semibold text-white shadow-sm hover:bg-[#2C3038] active:scale-95 transition-all disabled:opacity-50"
              >
                {n}
              </button>
            ))}
            <button
              type="button"
              onClick={handleClear}
              disabled={loading || !pin}
              className="flex h-12 items-center justify-center rounded-xl bg-[#1E2128] text-xs font-semibold uppercase tracking-wider text-[#9CA3AF] hover:text-white hover:bg-[#262A32] active:scale-95 transition-all disabled:opacity-40"
            >
              Clear
            </button>
            <button
              type="button"
              onClick={() => handleDigit("0")}
              disabled={loading}
              className="flex h-12 items-center justify-center rounded-xl bg-[#22252C] text-lg font-semibold text-white shadow-sm hover:bg-[#2C3038] active:scale-95 transition-all disabled:opacity-50"
            >
              0
            </button>
            <button
              type="button"
              onClick={handleBackspace}
              disabled={loading || !pin}
              title="Backspace"
              className="flex h-12 items-center justify-center rounded-xl bg-[#1E2128] text-[#9CA3AF] hover:text-white hover:bg-[#262A32] active:scale-95 transition-all disabled:opacity-40"
            >
              <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
                <path d="M21 4H8l-7 8 7 8h13a2 2 0 0 0 2-2V6a2 2 0 0 0-2-2z" />
                <line x1="18" y1="9" x2="12" y2="15" />
                <line x1="12" y1="9" x2="18" y2="15" />
              </svg>
            </button>
          </div>

          <button
            type="submit"
            disabled={loading || !pin.trim()}
            className="mt-1 flex h-12 w-full items-center justify-center gap-2 rounded-xl bg-gradient-to-r from-cyan-500 to-blue-600 font-semibold text-white shadow-lg shadow-cyan-500/20 hover:from-cyan-400 hover:to-blue-500 active:scale-[0.98] transition-all disabled:opacity-50"
          >
            {loading ? (
              <>
                <svg
                  width="18"
                  height="18"
                  viewBox="0 0 24 24"
                  fill="none"
                  stroke="currentColor"
                  strokeWidth="2"
                  className="animate-spin"
                >
                  <path d="M12 3a9 9 0 1 0 9 9" />
                </svg>
                <span>Verifying…</span>
              </>
            ) : (
              <span>Unlock Dashboard</span>
            )}
          </button>
        </form>
      </div>
    </div>
  );
}
