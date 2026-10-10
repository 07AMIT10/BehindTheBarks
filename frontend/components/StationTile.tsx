"use client";

import { useMemo } from "react";
import type { StationSummary, StationView } from "@/lib/types";
import { stationState } from "@/lib/phone";
import { batteryDisplay, batteryLevelClass, isTelemetryStale } from "@/lib/battery";
import EmotionIcon from "./EmotionIcon";

type Props = {
  http: string;
  token?: string | null;
  station: StationSummary;
  view?: StationView;
  isFocused?: boolean;
  onFocus: (id: string) => void;
};

export default function StationTile({
  http,
  token,
  station,
  view,
  isFocused = false,
  onFocus,
}: Props) {
  const synthStatus = useMemo(
    () => ({
      phone: station.phone,
      pipeline_status: station.pipeline_status,
      effective_mode: station.effective_mode,
    }),
    [station.phone, station.pipeline_status, station.effective_mode]
  );

  const state = stationState(synthStatus);
  const isLive = state === "live";
  const emotion = view?.current?.emotion ?? "unknown";

  const tokenParam = token ? `&token=${encodeURIComponent(token)}` : "";
  const videoSrc = isLive ? `${http}/video?station=${encodeURIComponent(station.id)}&view=grid${tokenParam}` : null;

  const dotColor =
    state === "live"
      ? "var(--accent)"
      : state === "idle"
      ? "var(--unknown)"
      : "var(--anxious, #f59e0b)";

  return (
    <button
      type="button"
      onClick={() => onFocus(station.id)}
      className={`group relative flex flex-col overflow-hidden rounded-xl border bg-surface text-left transition-all hover:border-accent/60 hover:shadow-md ${
        isFocused ? "border-accent ring-2 ring-accent/30" : "border-border"
      }`}
    >
      {/* Top Header / Badges */}
      <div className="flex items-center justify-between gap-2 border-b border-border/50 bg-surface-2/60 px-3 py-2">
        <div className="flex min-w-0 items-center gap-2">
          <span
            className="h-2 w-2 shrink-0 rounded-full"
            style={{
              background: dotColor,
              boxShadow: isLive ? "0 0 0 2px var(--accent-soft)" : "none",
            }}
          />
          <span className="truncate text-small font-semibold text-text">
            {station.name || "Station"}
          </span>
          <span className="truncate text-[11px] text-muted">
            ({station.dog_name || "Dog"})
          </span>
        </div>

        <div className="flex shrink-0 items-center gap-1.5">
          {(() => {
            const batteryPct = station.phone?.battery_pct ?? station.battery ?? null;
            const isCharging = Boolean(station.phone?.charging);
            const isStale = isTelemetryStale(station.phone?.last_frame_age_s);
            const bClass = batteryLevelClass(batteryPct, isStale);
            const bText = batteryDisplay(batteryPct, isStale);

            if (batteryPct === null && isStale) return null;

            return (
              <div
                className={`flex items-center gap-1 rounded px-1.5 py-0.5 text-[10px] font-mono transition-colors ${
                  bClass === "critical"
                    ? "bg-red-500/20 text-red-400 font-semibold"
                    : bClass === "low"
                    ? "bg-amber-500/20 text-amber-400 font-semibold"
                    : "bg-black/20 text-muted"
                }`}
                title={`Battery: ${bText}${isCharging ? " (charging)" : ""}${isStale ? " (stale)" : ""}`}
              >
                {isCharging ? (
                  <svg
                    width="11"
                    height="11"
                    viewBox="0 0 24 24"
                    fill="currentColor"
                    stroke="currentColor"
                    strokeWidth="1"
                    className="text-amber-400 animate-pulse"
                    aria-label="Charging"
                  >
                    <polygon points="13 2 3 14 12 14 11 22 21 10 12 10 13 2" />
                  </svg>
                ) : (
                  <svg
                    width="12"
                    height="12"
                    viewBox="0 0 24 24"
                    fill="none"
                    stroke="currentColor"
                    strokeWidth="2"
                    strokeLinecap="round"
                    strokeLinejoin="round"
                    aria-hidden="true"
                  >
                    <rect x="2" y="7" width="16" height="10" rx="2" ry="2" />
                    <line x1="22" y1="11" x2="22" y2="13" />
                  </svg>
                )}
                <span>{bText}</span>
              </div>
            );
          })()}
          <span className="rounded bg-white/10 px-1.5 py-0.5 text-[10px] font-semibold uppercase tracking-wider text-muted">
            {station.effective_mode}
          </span>
        </div>
      </div>

      {/* Snapshot / Preview Area */}
      <div className="relative aspect-video w-full overflow-hidden bg-black/40">
        {videoSrc ? (
          /* eslint-disable-next-line @next/next/no-img-element */
          <img
            src={videoSrc}
            alt={station.name}
            className="h-full w-full object-cover transition-transform duration-300 group-hover:scale-[1.02]"
            loading="lazy"
          />
        ) : (
          <div className="flex h-full w-full flex-col items-center justify-center gap-2 p-4 text-muted">
            <svg
              width="24"
              height="24"
              viewBox="0 0 24 24"
              fill="none"
              stroke="currentColor"
              strokeWidth="1.7"
              strokeLinecap="round"
              strokeLinejoin="round"
              aria-hidden="true"
            >
              <rect x="2" y="6" width="20" height="12" rx="2" />
              <circle cx="12" cy="12" r="3" />
            </svg>
            <span className="text-[12px] font-medium">Station offline</span>
          </div>
        )}

        {/* Emotion Chip Overlay */}
        {emotion !== "unknown" && (
          <div className="absolute bottom-2 left-2 flex items-center gap-1.5 rounded-lg border border-white/10 bg-black/70 px-2 py-1 text-[11px] font-medium text-white shadow-sm backdrop-blur-md">
            <EmotionIcon emotion={emotion} size={14} />
            <span className="capitalize">{emotion}</span>
          </div>
        )}

        {/* Focus prompt on hover */}
        <div className="absolute inset-0 flex items-center justify-center bg-black/40 opacity-0 backdrop-blur-[2px] transition-opacity group-hover:opacity-100">
          <span className="rounded-md bg-white/20 px-3 py-1.5 text-[12px] font-semibold text-white shadow">
            Focus Camera
          </span>
        </div>
      </div>
    </button>
  );
}
