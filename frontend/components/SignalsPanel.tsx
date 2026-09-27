"use client";

import { useState } from "react";
import type { Features } from "@/lib/contracts";
import { signalRows } from "@/lib/signals";
import Sparkline from "./Sparkline";

export default function SignalsPanel({ history, defaultOpen }: { history: Features[]; defaultOpen: boolean }) {
  const [open, setOpen] = useState(defaultOpen);
  const rows = signalRows(history);
  const live = rows.filter((r) => r.live).length;
  return (
    <section aria-label="Signals" className="flex flex-col rounded-xl border border-border bg-surface">
      <button type="button" onClick={() => setOpen(!open)} aria-expanded={open}
        className="flex h-[52px] items-center gap-2.5 px-5 text-left text-body font-semibold text-text">
        Signals<span className="text-[12px] font-medium text-muted">{live} live · from pose, face and audio</span>
        <span className="grow" />
        <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round"
          strokeLinejoin="round" style={{ transform: `rotate(${open ? 180 : 0}deg)` }} aria-hidden="true"><path d="M6 9l6 6 6-6" /></svg>
      </button>
      {open && (
        <div className="grid grid-cols-2 gap-px overflow-hidden rounded-b-xl border-t border-border bg-border sm:grid-cols-3">
          {rows.map((r) => (
            <div key={r.key} className="flex flex-col gap-1.5 bg-surface px-4 py-3.5">
              <div className="text-[11px] font-bold uppercase tracking-[0.06em] text-muted">{r.name}</div>
              <div className="flex items-baseline gap-1">
                <span className="font-mono text-[20px] font-medium">{r.value}</span>
                <span className="text-[12px] text-muted">{r.unit}</span>
              </div>
              <Sparkline points={r.points} />
            </div>
          ))}
        </div>
      )}
    </section>
  );
}
