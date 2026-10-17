"use client";

import { useRef } from "react";
import { useDialog } from "@/lib/useDialog";

type Props = {
  isOpen: boolean;
  onClose: () => void;
  dogName: string;
  location: string;
  stationConnected?: boolean;
  hasPairedStation?: boolean;
  batteryPct?: number | null;
  isCharging?: boolean;
  isBatteryStale?: boolean;
  hasMultipleStations?: boolean;
  showGrid?: boolean;
  onOpenProfile: () => void;
  onOpenMembers: () => void;
  onOpenPairModal: () => void;
  onOpenDeleteHousehold: () => void;
  onToggleGrid?: () => void;
  onLogout?: () => void;
  isOwner?: boolean;
  themeToggleNode?: React.ReactNode;
};

export default function MobileMenuDrawer({
  isOpen,
  onClose,
  dogName,
  location,
  stationConnected,
  hasPairedStation,
  batteryPct,
  isCharging,
  isBatteryStale,
  hasMultipleStations,
  showGrid,
  onOpenProfile,
  onOpenMembers,
  onOpenPairModal,
  onOpenDeleteHousehold,
  onToggleGrid,
  onLogout,
  isOwner = true,
  themeToggleNode,
}: Props) {
  const dialogRef = useRef<HTMLDivElement>(null);
  useDialog(dialogRef, isOpen, onClose);

  if (!isOpen) return null;

  return (
    <div
      role="dialog"
      aria-modal="true"
      aria-labelledby="mobile-menu-title"
      ref={dialogRef}
      className="fixed inset-0 z-50 flex items-end sm:items-center sm:justify-center bg-black/75 backdrop-blur-sm animate-in fade-in duration-200"
    >
      {/* Backdrop tap to close */}
      <button
        type="button"
        aria-label="Close menu"
        onClick={onClose}
        className="fixed inset-0 h-full w-full cursor-default"
      />

      <div
        className="relative z-10 w-full max-w-lg rounded-t-3xl sm:rounded-2xl border border-white/10 bg-[#16181d] p-5 shadow-2xl text-text max-h-[88dvh] overflow-y-auto"
        style={{ paddingBottom: "max(24px, env(safe-area-inset-bottom))" }}
      >
        {/* Header bar */}
        <div className="flex items-center justify-between border-b border-border/40 pb-3 mb-4">
          <div className="flex items-center gap-2.5">
            <div className="flex h-10 w-10 items-center justify-center rounded-xl bg-accent/15 border border-accent/30 text-accent font-bold text-lg">
              🐾
            </div>
            <div>
              <div id="mobile-menu-title" className="text-base font-bold text-white leading-tight">
                {dogName || "WagWatch"}
              </div>
              <div className="text-xs text-muted">
                {location ? `Feeding area · ${location}` : "Pet Monitor"}
              </div>
            </div>
          </div>
          <button
            type="button"
            onClick={onClose}
            aria-label="Close menu"
            className="flex h-10 w-10 items-center justify-center rounded-xl border border-white/10 text-muted hover:text-white transition-colors"
          >
            <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
              <line x1="18" y1="6" x2="6" y2="18" /><line x1="6" y1="6" x2="18" y2="18" />
            </svg>
          </button>
        </div>

        {/* Device & Station Status Card */}
        <div className="mb-4 rounded-xl border border-white/10 bg-white/5 p-3 flex items-center justify-between">
          <div className="flex items-center gap-3">
            <span className={`relative flex h-3 w-3 ${stationConnected ? "text-emerald-400" : hasPairedStation ? "text-amber-400" : "text-gray-400"}`}>
              <span className={`absolute inline-flex h-full w-full rounded-full opacity-75 ${stationConnected ? "animate-ping bg-emerald-400" : ""}`} />
              <span className={`relative inline-flex h-3 w-3 rounded-full ${stationConnected ? "bg-emerald-500" : hasPairedStation ? "bg-amber-500" : "bg-gray-500"}`} />
            </span>
            <div>
              <div className="text-xs font-semibold text-white">
                {stationConnected ? "Phone Station Live" : hasPairedStation ? "Station Paired (Idle)" : "No Station Paired"}
              </div>
              {batteryPct != null && (
                <div className="text-[11px] text-muted flex items-center gap-1.5 mt-0.5 font-mono">
                  <span>Battery: {batteryPct}%</span>
                  {isCharging && <span className="text-emerald-400">⚡ Charging</span>}
                  {isBatteryStale && <span className="text-amber-400/80">(stale)</span>}
                </div>
              )}
            </div>
          </div>
          <button
            type="button"
            onClick={() => {
              onClose();
              onOpenPairModal();
            }}
            className="h-9 px-3 rounded-lg bg-accent text-accent-fg text-xs font-bold hover:bg-accent/90 transition-colors shrink-0"
          >
            {hasPairedStation ? "Manage" : "Pair Phone"}
          </button>
        </div>

        {/* Menu Items List */}
        <div className="flex flex-col gap-1.5 mb-5">
          <button
            type="button"
            onClick={() => {
              onClose();
              onOpenProfile();
            }}
            className="flex h-12 w-full items-center gap-3.5 rounded-xl px-3.5 text-left text-sm font-semibold text-text hover:bg-white/5 transition-colors active:scale-[0.98]"
          >
            <span className="flex h-8 w-8 items-center justify-center rounded-lg bg-white/10 text-accent">
              <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                <path d="M12 20h9"/><path d="M16.5 3.5a2.12 2.12 0 0 1 3 3L7 19l-4 1 1-4z"/>
              </svg>
            </span>
            <span className="flex-1">Edit Pet Profile</span>
            <span className="text-muted text-xs">›</span>
          </button>

          <button
            type="button"
            onClick={() => {
              onClose();
              onOpenMembers();
            }}
            className="flex h-12 w-full items-center gap-3.5 rounded-xl px-3.5 text-left text-sm font-semibold text-text hover:bg-white/5 transition-colors active:scale-[0.98]"
          >
            <span className="flex h-8 w-8 items-center justify-center rounded-lg bg-white/10 text-emerald-400">
              <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                <path d="M17 21v-2a4 4 0 0 0-4-4H5a4 4 0 0 0-4 4v2"/><circle cx="9" cy="7" r="4"/><path d="M23 21v-2a4 4 0 0 0-3-3.87"/><path d="M16 3.13a4 4 0 0 1 0 7.75"/>
              </svg>
            </span>
            <span className="flex-1">Household Viewers & Devices</span>
            <span className="text-muted text-xs">›</span>
          </button>

          {hasMultipleStations && onToggleGrid && (
            <button
              type="button"
              onClick={() => {
                onClose();
                onToggleGrid();
              }}
              className="flex h-12 w-full items-center gap-3.5 rounded-xl px-3.5 text-left text-sm font-semibold text-text hover:bg-white/5 transition-colors active:scale-[0.98]"
            >
              <span className="flex h-8 w-8 items-center justify-center rounded-lg bg-white/10 text-blue-400">
                <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                  <rect x="3" y="3" width="7" height="7" /><rect x="14" y="3" width="7" height="7" /><rect x="14" y="14" width="7" height="7" /><rect x="3" y="14" width="7" height="7" />
                </svg>
              </span>
              <span className="flex-1">{showGrid ? "Single Camera View" : "Multi-Camera Grid"}</span>
              <span className="text-muted text-xs">›</span>
            </button>
          )}

          <div className="flex h-12 w-full items-center justify-between rounded-xl px-3.5 text-sm font-semibold text-text">
            <span className="flex items-center gap-3.5">
              <span className="flex h-8 w-8 items-center justify-center rounded-lg bg-white/10 text-amber-300">
                🌓
              </span>
              <span>Appearance</span>
            </span>
            <div>{themeToggleNode}</div>
          </div>
        </div>

        {/* Footer actions */}
        <div className="border-t border-border/40 pt-3 flex flex-col gap-2">
          {onLogout && (
            <button
              type="button"
              onClick={() => {
                onClose();
                onLogout();
              }}
              className="flex h-11 w-full items-center justify-center gap-2 rounded-xl border border-white/10 bg-white/5 text-xs font-semibold text-gray-300 hover:text-white hover:bg-white/10 transition-colors"
            >
              <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                <path d="M9 21H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h4"/><path d="M16 17l5-5-5-5M21 12H9"/>
              </svg>
              Sign out of Household
            </button>
          )}

          {isOwner && (
            <button
              type="button"
              onClick={() => {
                onClose();
                onOpenDeleteHousehold();
              }}
              className="flex h-11 w-full items-center justify-center gap-2 rounded-xl border border-red-500/20 bg-red-500/10 text-xs font-semibold text-red-300 hover:bg-red-500/20 transition-colors"
            >
              <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                <path d="M3 6h18"/><path d="M19 6v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6"/><path d="M8 6V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2"/>
              </svg>
              Delete Household
            </button>
          )}
        </div>
      </div>
    </div>
  );
}
