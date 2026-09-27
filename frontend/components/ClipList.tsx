"use client";

import { useEffect, useState } from "react";
import type { ClipMeta } from "@/lib/demo";
import { EMOTION_META } from "@/lib/emotions";
import { durationLabel } from "@/lib/format";
import EmotionIcon from "./EmotionIcon";

type Props = { http: string; activeId: string | null; onPick: (meta: ClipMeta) => void };

export default function ClipList({ http, activeId, onPick }: Props) {
  const [clips, setClips] = useState<ClipMeta[] | null>(null);
  useEffect(() => {
    let alive = true;
    fetch(`${http}/demo/clips`, { cache: "no-store" })
      .then((r) => (r.ok ? r.json() : []))
      .then((list: ClipMeta[]) => alive && setClips(list))
      .catch(() => alive && setClips([]));
    return () => { alive = false; };
  }, [http]);
  if (clips === null) return <div className="text-small text-muted">Loading clips…</div>;
  if (!clips.length) return <div className="text-small text-muted">No demo clips yet.</div>;
  return (
    <div role="listbox" aria-label="Demo clips" className="flex flex-col gap-1">
      {clips.map((c) => {
        const active = c.id === activeId;
        return (
          <button key={c.id} type="button" role="option" aria-selected={active} onClick={() => onPick(c)}
            className="flex h-11 items-center gap-3 rounded-md border border-border bg-surface px-3 text-left">
            <EmotionIcon emotion={c.emotion} size={20} />
            <span className="grow truncate text-small font-semibold">{c.name}</span>
            <span className="text-small font-semibold" style={{ color: `var(--${c.emotion}-fg)` }}>{EMOTION_META[c.emotion].label}</span>
            <span className="font-mono text-[12px] text-muted">{durationLabel(c.duration_s)}</span>
            {active && <span className="h-5 w-1 rounded-full" style={{ background: "var(--accent)" }} />}
          </button>
        );
      })}
    </div>
  );
}
