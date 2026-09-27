import { hhmmss } from "@/lib/format";
import type { Status } from "@/lib/types";

type Props = { connected: boolean; everConnected: boolean; reconnectAttempt: number; status: Status | null; lastFrameTs: number | null };

function Banner({ children }: { children: React.ReactNode }) {
  return <div role="status" className="flex flex-wrap items-center gap-x-3 gap-y-1 rounded-md border border-border bg-surface-2 px-4 py-2.5 text-small text-text-soft">{children}</div>;
}

export default function SystemNotice({ connected, everConnected, reconnectAttempt, status, lastFrameTs }: Props) {
  if (!connected && everConnected) {
    return (
      <Banner>
        <span className="font-bold text-text">Reconnecting to Claude Pet</span>
        <span>attempt {reconnectAttempt}</span>
        {lastFrameTs && <span className="font-mono">last frame {hhmmss(lastFrameTs)}</span>}
      </Banner>
    );
  }
  if (status?.pipeline_status?.state === "stalled") {
    return (
      <Banner>
        <span className="font-bold text-text">Reconnecting to camera</span>
        {lastFrameTs && <span className="font-mono">last frame {hhmmss(lastFrameTs)}</span>}
        <span>The camera phone dropped off Wi‑Fi. Keep it on and near the router.</span>
      </Banner>
    );
  }
  if (status?.llm?.enabled && !status.llm.online) {
    return (
      <Banner>
        <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" aria-hidden="true"><circle cx="12" cy="12" r="9" /><path d="M12 8h.01M11 12h1v4h1" /></svg>
        <span>AI is unreachable, so readings come from rules and may be less nuanced. Retrying automatically.</span>
      </Banner>
    );
  }
  return null;
}
