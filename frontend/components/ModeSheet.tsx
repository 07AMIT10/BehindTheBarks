import React, { useState, useRef } from "react";
import { useDialog } from "@/lib/useDialog";
import type { ModeRequestResult, ViewMode } from "../lib/types";
import { CONFIGURABLE_MODES, isModeAllowed, stationAnswerLabel } from "../lib/modes";

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
  /** The phone's answer to the last raise prompt (status.phone.last_mode_request_result). */
  requestResult?: ModeRequestResult | null;
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
  requestResult = null,
}) => {
  const [selectedMode, setSelectedMode] = useState<ViewMode | null>(null);
  const [noticeMsg, setNoticeMsg] = useState<string | null>(null);
  const [peekMsg, setPeekMsg] = useState<string | null>(null);

  const dialogRef = useRef<HTMLDivElement>(null);
  useDialog(dialogRef, isOpen, onClose);
  if (!isOpen) return null;

  const handleSelectMode = async (mode: ViewMode) => {
    if (mode === effectiveMode) return;
    if (!isModeAllowed(mode, modeCeiling)) return;
    if (!isOwner) return;

    setSelectedMode(mode);
    try {
      const res = await onSetMode(mode);
      if (res?.status === "pending") {
        setNoticeMsg("Mode request sent to station phone");
      } else {
        setNoticeMsg(null);
        setTimeout(() => onClose(), 350);
      }
    } catch {
      setSelectedMode(null);
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

  return (
    <div
      role="dialog"
      ref={dialogRef}
      tabIndex={-1}
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

        {/* Optional status notice (non-blocking) */}
        {noticeMsg && (
          <div className="mt-4 flex items-center justify-between rounded-xl border border-sky-500/30 bg-sky-500/10 p-3 text-xs text-sky-200">
            <span>
              {noticeMsg}
              {requestResult && (
                <span className="ml-2 text-[11px] text-sky-300/80">Station: {stationAnswerLabel(requestResult.result)}</span>
              )}
            </span>
            <button
              onClick={() => setNoticeMsg(null)}
              className="rounded px-2 py-0.5 font-medium bg-sky-600/40 hover:bg-sky-600/60"
            >
              Dismiss
            </button>
          </div>
        )}

        {/* Mode Options List */}
        <div className="mt-4 space-y-2">
          {CONFIGURABLE_MODES.filter((m) => m.enabled).map((config) => {
            const mode = config.mode;
            const isCurrent = (selectedMode ? selectedMode === mode : mode === effectiveMode);
            const isAllowed = isModeAllowed(mode, modeCeiling);
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
                  <span className="text-xl">{config.icon}</span>
                  <div>
                    <div className="flex items-center gap-2">
                      <span className="font-medium text-sm text-neutral-100">{config.label}</span>
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
                    <p className="mt-0.5 text-xs text-neutral-400">{config.desc}</p>
                  </div>
                </div>

                {!isAllowed && (
                  <span
                    className="ml-2 rounded border border-neutral-700 px-2 py-0.5 text-[11px] text-neutral-400"
                    title={`The station cap is currently ${stationCap}`}
                  >
                    Capped
                  </span>
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
