/**
 * Types and pure helpers for Debug & Diagnostics Drawer (T6.3).
 */

export type ServerMetrics = {
  rss_mb: number;
  cpu_pct: number;
  threads: number;
  uptime_s: number;
  loop_lag_ms: number;
};

export type HubMetrics = {
  clients: number;
  queue_depths: number[];
};

export type TelemetrySample = {
  ts: number;
  battery_pct?: number;
  charging?: boolean;
  plugged?: string;
  temp_c?: number;
  thermal_status?: number;
  thermal_headroom?: number | null;
  inference_ms?: Record<string, number>;
  preview_kbps?: number;
  preview_fps?: number;
  preview_dropped?: number;
  ws_rtt_ms?: number;
  queue_bytes?: number;
  spool_bytes?: number;
  mem_mb?: number;
  degrade_level?: string;
  [key: string]: unknown;
};

export type DebugMetrics = {
  server: ServerMetrics;
  hub: HubMetrics;
  households: number;
  station: {
    latest_telemetry: TelemetrySample | null;
    series: TelemetrySample[];
  };
  downlinks: {
    sent: number;
    dropped: number;
  };
  drain_pct_per_h: number | null;
};

/**
 * Converts an array of numbers into SVG polyline points string within the specified width & height.
 * Inverts y so higher values appear at the top.
 */
export function seriesToSparkline(
  values: number[],
  width = 100,
  height = 24,
  customMin?: number,
  customMax?: number
): string {
  if (!values || values.length === 0) return "";
  if (values.length === 1) {
    const y = height / 2;
    return `0,${y} ${width},${y}`;
  }

  let min = customMin !== undefined ? customMin : Math.min(...values);
  let max = customMax !== undefined ? customMax : Math.max(...values);

  if (min === max) {
    min -= 1;
    max += 1;
  }

  const range = max - min;
  const stepX = width / (values.length - 1);

  return values
    .map((v, i) => {
      const x = Math.round(i * stepX * 10) / 10;
      const normalized = (v - min) / range;
      // Invert Y: 1.0 (max) -> 2 (near top), 0.0 (min) -> height - 2 (near bottom)
      const y = Math.round((height - 2 - normalized * (height - 4)) * 10) / 10;
      return `${x},${y}`;
    })
    .join(" ");
}

/**
 * Evenly downsamples a series to at most maxPoints while preserving endpoints.
 */
export function downsampleSeries<T>(items: T[], maxPoints = 50): T[] {
  if (!items || items.length <= maxPoints) return items ? [...items] : [];
  if (maxPoints <= 1) return [items[items.length - 1]];

  const result: T[] = [];
  const step = (items.length - 1) / (maxPoints - 1);

  for (let i = 0; i < maxPoints; i++) {
    const index = Math.min(Math.round(i * step), items.length - 1);
    result.push(items[index]);
  }
  return result;
}

/**
 * Extracts numeric values from a series using a selector.
 */
export function extractSeriesValues(
  series: TelemetrySample[],
  selector: (s: TelemetrySample) => number | null | undefined
): number[] {
  if (!series) return [];
  const out: number[] = [];
  for (const s of series) {
    const val = selector(s);
    if (val !== null && val !== undefined && !Number.isNaN(val)) {
      out.push(val);
    }
  }
  return out;
}
