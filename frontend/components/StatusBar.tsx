import { deviceLabel } from "@/lib/phone";
import type { Status } from "@/lib/types";

type Props = {
  connected: boolean;
  everConnected: boolean;
  reconnectAttempt: number;
  status: Status | null;
  onMode?: (mode: "live" | "demo") => void;
};

export default function StatusBar({ connected, everConnected, reconnectAttempt, status, onMode }: Props) {
  const llm = status?.llm;
  const aiOk = Boolean(llm?.enabled && llm.online);
  const latency = llm?.last_call?.latency_ms;
  const mode = status?.mode ?? (status?.demo_mode ? "demo" : "live");
  const demoAvailable = Boolean(status?.modes?.includes("demo")) && Boolean(onMode);
  const conn = connected ? "Connected" : everConnected ? `Reconnecting · attempt ${reconnectAttempt}` : "Connecting…";
  return (
    <div role="status" className="flex min-h-10 flex-wrap items-center gap-x-5 gap-y-2 rounded-md border border-border bg-surface px-4 py-2 text-small text-muted">
      <div className="flex items-center gap-2 font-semibold text-text">
        <span className="h-2 w-2 rounded-full"
          style={{ background: connected ? "var(--accent)" : "var(--unknown)", boxShadow: connected ? "0 0 0 3px var(--accent-soft)" : "none" }} />
        {conn}
      </div>
      <div className="flex items-center gap-1.5">
        <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.75" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true"><rect x="7" y="2.5" width="10" height="19" rx="2.5" /><path d="M11 18.5h2" /></svg>
        {deviceLabel(status)}
      </div>
      <div className="font-mono">{Math.round(status?.fps ?? 0)} fps</div>
      <div className="flex items-center gap-1.5">
        {aiOk ? (
          <>AI · {llm?.model || "vision"}{latency !== undefined && <span className="font-mono">{Math.round(latency)} ms</span>}</>
        ) : (
          <><span className="inline-block h-2 w-2 rounded-full border-[1.5px] border-muted" />{llm?.enabled ? "AI offline · rules only" : "Rules only"}</>
        )}
      </div>
      <div className="grow" />
      <div role="group" aria-label="Mode" className="flex gap-0.5 rounded-lg bg-surface-2 p-[3px]">
        {(["live", "demo"] as const).map((m) => {
          const active = mode === m;
          const disabled = m === "demo" ? !demoAvailable : !onMode && !active;
          return (
            <button key={m} type="button" aria-pressed={active} disabled={disabled || active}
              title={m === "demo" && !demoAvailable ? "Demo mode arrives with the demo clips" : undefined}
              onClick={() => onMode?.(m)}
              className={`flex h-7 items-center gap-1.5 rounded-sm px-3 text-[12px] font-semibold ${active ? "bg-surface text-text shadow-sm" : "text-muted"} disabled:cursor-default ${disabled && !active ? "opacity-50" : ""}`}>
              {m === "live" && <span className="h-1.5 w-1.5 rounded-full" style={{ background: "var(--accent)" }} />}
              {m === "live" ? "Live" : "Demo"}
            </button>
          );
        })}
      </div>
    </div>
  );
}
