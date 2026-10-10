import React, { useEffect, useState, useRef } from "react";
import { useDialog } from "@/lib/useDialog";
import type { ViewMode } from "../lib/types";
import { MODE_METADATA, MODE_ORDER, isModeAllowed, modeRank } from "../lib/modes";

interface ModeSheetProps {
  isOpen: boolean;
  onClose: () => void;
  effectiveMode: ViewMode;
  stationCap: ViewMode;
  householdMode: ViewMode;
  modeCeiling?: ViewMode;
  isOwner?: boolean;
  onSetMode: (mode: ViewMode) => Promise<{ status: "applied" | "pending"; mode: ViewMode } | undefined>;
  onPeek?: () => Promise<boolean>;
}

export const ModeSheet: React.FC<ModeSheetProps> = ({
  isOpen,
  onClose,
  effectiveMode,
  stationCap,
  householdMode,
  modeCeiling = "full",
  isOwner = true,
  onSetMode,
  onPeek,
}) => {
  const [pendingMode, setPendingMode] = useState<ViewMode | null>(null);
  const [countdown, setCountdown] = useState<number>(60);
  const [timedOut, setTimedOut] = useState<boolean>(false);
  const [peekMsg, setPeekMsg] = useState<string | null>(null);

  // Countdown timer for pending approval
  useEffect(() => {
    if (!pendingMode || timedOut || modeRank(effectiveMode) >= modeRank(pendingMode)) return;
    const interval = setInterval(() => {
      setCountdown((prev) => {
        if (prev <= 1) {
          clearInterval(interval);
          setTimedOut(true);
          return 0;
        }
        return prev - 1;
      });
    }, 1000);
    return () => clearInterval(interval);
  }, [pendingMode, timedOut, effectiveMode]);

  const dialogRef = useRef<HTMLDivElement>(null);
  useDialog(dialogRef, isOpen, onClose);
  if (!isOpen) return null;

  const handleSelectMode = async (mode: ViewMode) => {
    if (mode === effectiveMode) return;
    if (!isModeAllowed(mode, modeCeiling)) return;
    if (!isOwner) return;

    setTimedOut(false);
    const res = await onSetMode(mode);
    if (res?.status === "pending") {
      setPendingMode(mode);
      setCountdown(60);
    } else {
      setPendingMode(null);
    }
  };

  const handlePeek = async () => {
    if (!onPeek) return;
    setPeekMsg("Requesting 15s peek...");
    const ok = await onPeek();
    if (ok) {
      setPeekMsg("Peek active for 15s! Stream temporarily unblurred.");
    } else {
      setPeekMsg("Peek denied: station cap is not full.");
    }
    setTimeout(() => setPeekMsg(null), 4000);
  };

  const showPendingNotice = pendingMode !== null && (timedOut || modeRank(effectiveMode) < modeRank(pendingMode));

  return (
    <div
      role="dialog" ref={dialogRef} tabIndex={-1}
      aria-modal="true"
      className="fixed inset-0 z-50 flex items-end justify-center bg-black/60 backdrop-blur-sm sm:items-center sm:p-4"
      onClick={onClose}
    >
      <div
        className="w-full max-w-md rounded-t-2xl border border-neutral-800 bg-neutral-900 p-6 text-white shadow-2xl sm:rounded-2xl"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="flex items-center justify-between border-b border-neutral-800 pb-3">
          <div>
            <h3 className="text-lg font-semibold tracking-tight">Viewing Mode</h3>
            <p className="text-xs text-neutral-400">
              Station allows up to: <span className="font-medium text-neutral-200 capitalize">{stationCap}</span>
            </p>
          </div>
          <button
            onClick={onClose}
            className="flex h-11 w-11 items-center justify-center rounded-lg text-neutral-400 hover:bg-neutral-800 hover:text-white md:h-8 md:w-8"
            aria-label="Close"
          >
            ✕
          </button>
        </div>

        {/* Pending Approval Notice */}
        {showPendingNotice && (
          <div className="mt-4 rounded-xl border border-amber-500/30 bg-amber-500/10 p-3.5 text-xs text-amber-200">
            {timedOut ? (
              <div className="flex items-center justify-between">
                <span>Request timed out or denied on the phone.</span>
                <button
                  onClick={() => {
                    setPendingMode(null);
                    setTimedOut(false);
                  }}
                  className="rounded px-2 py-1 font-medium bg-amber-600/40 hover:bg-amber-600/60"
                >
                  Dismiss
                </button>
              </div>
            ) : (
              <div className="flex items-center justify-between">
                <span className="flex items-center gap-2">
                  <span className="inline-block h-2 w-2 animate-ping rounded-full bg-amber-400" />
                  Waiting for approval on the phone…
                </span>
                <span className="font-mono font-medium text-amber-300">{countdown}s</span>
              </div>
            )}
          </div>
        )}

        {/* Mode Options List */}
        <div className="mt-4 space-y-2">
          {MODE_ORDER.map((mode) => {
            const meta = MODE_METADATA[mode];
            const isCurrent = mode === effectiveMode;
            const isAllowed = isModeAllowed(mode, modeCeiling);
            const isPendingThisMode = pendingMode === mode && !timedOut && modeRank(effectiveMode) < modeRank(pendingMode);
            const disabled = !isAllowed || (!isOwner && !isCurrent);

            return (
              <button
                key={mode}
                disabled={disabled}
                onClick={() => handleSelectMode(mode)}
                className={`flex w-full items-start justify-between rounded-xl border p-3.5 text-left transition-all ${
                  isCurrent
                    ? "border-emerald-500/50 bg-emerald-500/10"
                    : disabled
                      ? "cursor-not-allowed border-neutral-800/60 bg-neutral-900/40 opacity-50"
                      : "border-neutral-800 bg-neutral-800/50 hover:border-neutral-700 hover:bg-neutral-800"
                }`}
              >
                <div className="flex items-start gap-3">
                  <span className="text-xl">{meta.icon}</span>
                  <div>
                    <div className="flex items-center gap-2">
                      <span className="font-medium text-sm text-neutral-100">{meta.label}</span>
                      {isCurrent && (
                        <span className="rounded-full bg-emerald-500/20 px-2 py-0.5 text-[12px] font-semibold text-emerald-400">
                          Active
                        </span>
                      )}
                      {mode === householdMode && !isCurrent && (
                        <span className="rounded-full bg-neutral-700 px-2 py-0.5 text-[12px] text-neutral-300">
                          Policy
                        </span>
                      )}
                    </div>
                    <p className="mt-0.5 text-xs text-neutral-400">{meta.desc}</p>
                  </div>
                </div>

                {!isAllowed && (
                  <span
                    className="flex items-center gap-1 text-[12px] text-neutral-400"
                    title="Exceeds your access ceiling"
                  >
                    🔒 Locked
                  </span>
                )}

                {isPendingThisMode && (
                  <span className="text-xs text-amber-400 animate-pulse font-medium">Pending…</span>
                )}
              </button>
            );
          })}
        </div>

        {/* Peek Action for Limited Mode */}
        {effectiveMode === "limited" && onPeek && (
          <div className="mt-4 border-t border-neutral-800 pt-4">
            <button
              onClick={handlePeek}
              className="w-full rounded-xl bg-neutral-800 py-2.5 text-xs font-medium text-neutral-200 transition hover:bg-neutral-700 hover:text-white"
            >
              👁️ Request 15-second Video Peek
            </button>
            {peekMsg && <p className="mt-2 text-center text-xs text-neutral-300">{peekMsg}</p>}
          </div>
        )}
      </div>
    </div>
  );
};
