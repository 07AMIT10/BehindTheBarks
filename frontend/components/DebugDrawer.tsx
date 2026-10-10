"use client";

import { useEffect, useState, useRef } from "react";
import { useDialog } from "@/lib/useDialog";
import Sparkline from "./Sparkline";
import {
  type DebugMetrics,
  extractSeriesValues,
  seriesToSparkline,
} from "@/lib/debug";

type Props = {
  open: boolean;
  onClose: () => void;
  http: string;
  token?: string;
  stationId?: string;
  stationName?: string;
  llmInfo?: { provider?: string; model?: string };
  clockOffset?: number | null;
  eventFps?: number;
  previewFps?: number;
};

export default function DebugDrawer({
  open,
  onClose,
  http,
  token,
  stationId,
  stationName,
  llmInfo,
  clockOffset,
  eventFps,
  previewFps,
}: Props) {
  const [metrics, setMetrics] = useState<DebugMetrics | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [copied, setCopied] = useState(false);
  const loading = !metrics && !error;

  useEffect(() => {
    if (!open) return;

    let mounted = true;
    const fetchMetrics = async () => {
      try {
        const params = new URLSearchParams();
        if (stationId && stationId !== "default") params.set("station", stationId);
        const qs = params.toString();
        const url = `${http}/debug/metrics${qs ? `?${qs}` : ""}`;
        const headers: Record<string, string> = {};
        if (token) headers.Authorization = `Bearer ${token}`;

        const res = await fetch(url, { headers, credentials: "include" });
        if (!res.ok) {
          throw new Error(`Failed to load metrics: ${res.status}`);
        }
        const data = (await res.json()) as DebugMetrics;
        if (mounted) {
          setMetrics(data);
          setError(null);
        }
      } catch (err) {
        if (mounted) setError(err instanceof Error ? err.message : "Error fetching metrics");
      }
    };

    void fetchMetrics();
    const interval = setInterval(fetchMetrics, 5000);

    return () => {
      mounted = false;
      clearInterval(interval);
    };
  }, [open, http, token, stationId]);

  const dialogRef = useRef<HTMLDivElement>(null);
  useDialog(dialogRef, open, onClose);
  if (!open) return null;

  const latest = metrics?.station.latest_telemetry;
  const series = metrics?.station.series || [];

  const batteryPoints = seriesToSparkline(
    extractSeriesValues(series, (s) => s.battery_pct),
    100,
    24,
    0,
    100
  );

  const tempPoints = seriesToSparkline(
    extractSeriesValues(series, (s) => s.temp_c),
    100,
    24,
    20,
    60
  );

  const rttPoints = seriesToSparkline(
    extractSeriesValues(series, (s) => s.ws_rtt_ms),
    100,
    24
  );

  const detectorPoints = seriesToSparkline(
    extractSeriesValues(series, (s) => s.inference_ms?.detector),
    100,
    24
  );

  const handleCopyJson = () => {
    if (!metrics) return;
    const fullJson = JSON.stringify(
      {
        ...metrics,
        developer_chips: {
          llm: llmInfo,
          clock_offset_s: clockOffset,
          event_fps: eventFps,
          preview_fps: previewFps,
        },
      },
      null,
      2
    );
    void navigator.clipboard.writeText(fullJson);
    setCopied(true);
    setTimeout(() => setCopied(false), 2000);
  };

  return (
    <div
      className="fixed inset-0 z-50 flex justify-end bg-black/60 backdrop-blur-sm transition-opacity"
      role="dialog" ref={dialogRef} tabIndex={-1}
      aria-modal="true"
      aria-labelledby="debug-drawer-title"
    >
      <div className="flex h-full w-full max-w-md flex-col bg-surface border-l border-border shadow-2xl overflow-hidden">
        {/* Header */}
        <div className="flex items-center justify-between border-b border-border px-5 py-3.5 bg-surface-2">
          <div className="flex items-center gap-2">
            <svg
              width="18"
              height="18"
              viewBox="0 0 24 24"
              fill="none"
              stroke="currentColor"
              strokeWidth="2"
              strokeLinecap="round"
              strokeLinejoin="round"
              className="text-accent"
              aria-hidden="true"
            >
              <path d="M12.22 2h-.44a2 2 0 0 0-2 2v.18a2 2 0 0 1-1 1.73l-.43.25a2 2 0 0 1-2 0l-.15-.08a2 2 0 0 0-2.73.73l-.22.38a2 2 0 0 0 .73 2.73l.15.1a2 2 0 0 1 1 1.72v.51a2 2 0 0 1-1 1.74l-.15.09a2 2 0 0 0-.73 2.73l.22.38a2 2 0 0 0 2.73.73l.15-.08a2 2 0 0 1 2 0l.43.25a2 2 0 0 1 1 1.73V20a2 2 0 0 0 2 2h.44a2 2 0 0 0 2-2v-.18a2 2 0 0 1 1-1.73l.43-.25a2 2 0 0 1 2 0l.15.08a2 2 0 0 0 2.73-.73l.22-.39a2 2 0 0 0-.73-2.73l-.15-.08a2 2 0 0 1-1-1.74v-.5a2 2 0 0 1 1-1.74l.15-.09a2 2 0 0 0 .73-2.73l-.22-.38a2 2 0 0 0-2.73-.73l-.15.08a2 2 0 0 1-2 0l-.43-.25a2 2 0 0 1-1-1.73V4a2 2 0 0 0-2-2z" />
              <circle cx="12" cy="12" r="3" />
            </svg>
            <h2 id="debug-drawer-title" className="text-body font-bold text-text">
              Diagnostics & Debug Mode
            </h2>
          </div>
          <button
            type="button"
            onClick={onClose}
            className="rounded p-1 text-muted hover:text-white transition-colors"
            aria-label="Close drawer"
          >
            <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
              <line x1="18" y1="6" x2="6" y2="18" /><line x1="6" y1="6" x2="18" y2="18" />
            </svg>
          </button>
        </div>

        {/* Scrollable Content */}
        <div className="flex-1 overflow-y-auto p-5 space-y-5 text-[12px] font-mono">
          {error && (
            <div className="rounded-lg border border-red-500/40 bg-red-500/10 p-3 text-red-300">
              {error}
            </div>
          )}

          {/* Developer Info Chips */}
          <div className="space-y-1.5">
            <div className="text-[12px] font-semibold uppercase tracking-wider text-muted font-sans">
              System Chips
            </div>
            <div className="flex flex-wrap gap-1.5 font-mono text-[12px]">
              <span className="rounded bg-black/30 border border-border px-2 py-0.5 text-muted">
                LLM: <strong className="text-text">{llmInfo?.provider || "none"}</strong> ({llmInfo?.model || "off"})
              </span>
              <span className="rounded bg-black/30 border border-border px-2 py-0.5 text-muted">
                Clock Offset:{" "}
                <strong className="text-text">
                  {clockOffset !== null && clockOffset !== undefined
                    ? `${clockOffset >= 0 ? "+" : ""}${clockOffset.toFixed(2)}s`
                    : "0.00s"}
                </strong>
              </span>
              <span className="rounded bg-black/30 border border-border px-2 py-0.5 text-muted">
                Events: <strong className="text-text">{eventFps?.toFixed(1) ?? "?"} fps</strong>
              </span>
              <span className="rounded bg-black/30 border border-border px-2 py-0.5 text-muted">
                Preview: <strong className="text-text">{previewFps?.toFixed(1) ?? "?"} fps</strong>
              </span>
            </div>
          </div>

          {/* Server Resources */}
          {metrics && (
            <div className="space-y-2">
              <div className="text-[12px] font-semibold uppercase tracking-wider text-muted font-sans">
                Backend Server Resources
              </div>
              <div className="grid grid-cols-2 gap-2 text-[12px]">
                <div className="rounded border border-border bg-black/20 p-2">
                  <div className="text-muted">Process RSS</div>
                  <div className="text-[14px] font-bold text-text">{metrics.server.rss_mb} MB</div>
                </div>
                <div className="rounded border border-border bg-black/20 p-2">
                  <div className="text-muted">CPU Usage</div>
                  <div className="text-[14px] font-bold text-text">{metrics.server.cpu_pct}%</div>
                </div>
                <div className="rounded border border-border bg-black/20 p-2">
                  <div className="text-muted">Loop Lag</div>
                  <div className="text-[14px] font-bold text-text">{metrics.server.loop_lag_ms} ms</div>
                </div>
                <div className="rounded border border-border bg-black/20 p-2">
                  <div className="text-muted">WS Clients / Queues</div>
                  <div className="text-[14px] font-bold text-text">
                    {metrics.hub.clients} ({metrics.hub.queue_depths.join(", ") || "0"})
                  </div>
                </div>
                <div className="rounded border border-border bg-black/20 p-2">
                  <div className="text-muted">Server Uptime</div>
                  <div className="text-[14px] font-bold text-text">{Math.round(metrics.server.uptime_s)}s</div>
                </div>
                <div className="rounded border border-border bg-black/20 p-2">
                  <div className="text-muted">Downlinks Sent / Dropped</div>
                  <div className="text-[14px] font-bold text-text">
                    {metrics.downlinks.sent} / {metrics.downlinks.dropped}
                  </div>
                </div>
              </div>
            </div>
          )}

          {/* Station Telemetry Sparklines */}
          {latest ? (
            <div className="space-y-3">
              <div className="flex items-center justify-between font-sans">
                <span className="text-[12px] font-semibold uppercase tracking-wider text-muted">
                  Station Telemetry: {stationName || "Active"}
                </span>
                {metrics?.drain_pct_per_h !== null && metrics?.drain_pct_per_h !== undefined && (
                  <span className="text-[12px] text-amber-400 font-mono">
                    Drain: ~{metrics.drain_pct_per_h}%/hr
                  </span>
                )}
              </div>

              {/* Battery */}
              <div className="rounded border border-border bg-black/20 p-2.5 space-y-1">
                <div className="flex justify-between items-center text-[12px]">
                  <span className="text-muted">Battery Level</span>
                  <span className="font-bold text-text">
                    {latest.battery_pct ?? "?"}% {latest.charging ? "(charging)" : ""}
                  </span>
                </div>
                {batteryPoints && <Sparkline points={batteryPoints} />}
              </div>

              {/* Temperature */}
              <div className="rounded border border-border bg-black/20 p-2.5 space-y-1">
                <div className="flex justify-between items-center text-[12px]">
                  <span className="text-muted">Temperature</span>
                  <span className="font-bold text-text">{latest.temp_c ?? "?"}°C</span>
                </div>
                {tempPoints && <Sparkline points={tempPoints} />}
              </div>

              {/* WebSocket RTT */}
              <div className="rounded border border-border bg-black/20 p-2.5 space-y-1">
                <div className="flex justify-between items-center text-[12px]">
                  <span className="text-muted">WebSocket RTT</span>
                  <span className="font-bold text-text">{latest.ws_rtt_ms ?? "?"} ms</span>
                </div>
                {rttPoints && <Sparkline points={rttPoints} />}
              </div>

              {/* Detector Latency */}
              <div className="rounded border border-border bg-black/20 p-2.5 space-y-1">
                <div className="flex justify-between items-center text-[12px]">
                  <span className="text-muted">Detector Inference</span>
                  <span className="font-bold text-text">
                    {latest.inference_ms?.detector ?? "?"} ms
                  </span>
                </div>
                {detectorPoints && <Sparkline points={detectorPoints} />}
              </div>

              {/* Raw Latest Key-Value Table */}
              <div className="space-y-1">
                <div className="text-[12px] font-semibold uppercase tracking-wider text-muted font-sans">
                  Raw Telemetry Values
                </div>
                <div className="rounded border border-border bg-black/30 p-2 max-h-48 overflow-y-auto">
                  <table className="w-full text-[12px]">
                    <tbody>
                      {Object.entries(latest).map(([k, v]) => (
                        <tr key={k} className="border-b border-white/5 last:border-0">
                          <td className="py-0.5 text-muted pr-2">{k}</td>
                          <td className="py-0.5 text-text text-right truncate max-w-[180px]">
                            {typeof v === "object" ? JSON.stringify(v) : String(v)}
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              </div>
            </div>
          ) : (
            <div className="rounded border border-dashed border-border p-4 text-center text-muted">
              {loading ? "Loading telemetry..." : "No station telemetry received yet."}
            </div>
          )}
        </div>

        {/* Footer */}
        <div className="border-t border-border p-3 bg-surface-2 flex items-center justify-between">
          <button
            type="button"
            onClick={handleCopyJson}
            disabled={!metrics}
            className="flex items-center gap-1.5 rounded-lg border border-border bg-surface px-3 py-1.5 text-[12px] font-semibold text-text hover:bg-surface-2 transition-colors disabled:opacity-50"
          >
            <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
              <rect x="9" y="9" width="13" height="13" rx="2" ry="2" /><path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1" />
            </svg>
            <span>{copied ? "Copied Diagnostics!" : "Copy Diagnostics JSON"}</span>
          </button>
          <button
            type="button"
            onClick={onClose}
            className="rounded-lg border border-border px-3 py-1.5 text-[12px] font-semibold text-muted hover:text-white transition-colors"
          >
            Close
          </button>
        </div>
      </div>
    </div>
  );
}
