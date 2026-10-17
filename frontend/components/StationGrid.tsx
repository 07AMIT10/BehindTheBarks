"use client";

import type { StationSummary, StationView } from "@/lib/types";
import StationTile from "./StationTile";

type Props = {
  http: string;
  token?: string | null;
  stations: StationSummary[];
  stationViews: Record<string, StationView>;
  focusedStation?: string;
  onFocus: (id: string) => void;
};

export default function StationGrid({
  http,
  token,
  stations,
  stationViews,
  focusedStation,
  onFocus,
}: Props) {
  if (stations.length === 0) {
    return (
      <div className="flex h-48 w-full items-center justify-center rounded-xl border border-dashed border-border bg-surface/50 text-small text-muted">
        No active stations in this household
      </div>
    );
  }

  const gridColsClass =
    stations.length === 1
      ? "grid-cols-1"
      : stations.length === 2
      ? "grid-cols-1 sm:grid-cols-2"
      : "grid-cols-1 sm:grid-cols-2";

  return (
    <div className={`grid gap-3 sm:gap-4 ${gridColsClass}`}>
      {stations.map((st) => (
        <StationTile
          key={st.id}
          http={http}
          token={token}
          station={st}
          view={stationViews[st.id]}
          isFocused={focusedStation === st.id}
          onFocus={onFocus}
        />
      ))}
    </div>
  );
}
