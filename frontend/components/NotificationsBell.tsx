"use client";

import { useState } from "react";
import { NEGATIVE } from "@/lib/emotions";
import { hhmm } from "@/lib/format";
import type { NotificationItem } from "@/lib/types";
import EmotionIcon from "./EmotionIcon";

type Props = { notifications: NotificationItem[]; dogName: string; onOpen: () => void };

export default function NotificationsBell({ notifications, dogName, onOpen }: Props) {
  const [open, setOpen] = useState(false);
  const unread = notifications.filter((n) => !n.read).length;
  const toggle = () => {
    if (!open) onOpen();
    setOpen(!open);
  };
  return (
    <div className="relative">
      <button type="button" onClick={toggle} aria-expanded={open}
        aria-label={unread ? `Notifications, ${unread} new` : "Notifications"}
        className="relative flex h-11 w-11 items-center justify-center rounded-md border border-border bg-surface text-text">
        <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.75" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true"><path d="M6 16V11a6 6 0 0 1 12 0v5l1.5 2h-15z" /><path d="M10 20.5a2 2 0 0 0 4 0" /></svg>
        {unread > 0 && (
          <span className="absolute right-1.5 top-1.5 flex h-4 min-w-4 items-center justify-center rounded-full bg-accent px-1 text-[10px] font-bold text-accent-fg">{unread}</span>
        )}
      </button>
      {open && (
        <div className="absolute right-0 top-12 z-40 flex max-h-96 w-80 flex-col overflow-y-auto rounded-lg border border-border bg-surface p-2 shadow-[var(--shadow-toast)]">
          {notifications.length === 0 && <div className="p-4 text-small text-muted">No notifications yet.</div>}
          {[...notifications].reverse().map((n) => (
            <div key={n.id} className="flex gap-3 rounded-md p-2 hover:bg-surface-2">
              <EmotionIcon emotion={n.state.emotion} size={22} />
              <div className="flex min-w-0 grow flex-col">
                <div className="flex items-baseline gap-2">
                  <div className="grow truncate text-small font-bold">{dogName} {NEGATIVE.has(n.state.emotion) ? "seems" : "is"} {n.state.emotion}</div>
                  <div className="font-mono text-[11px] text-muted">{hhmm(n.ts)}</div>
                </div>
                <div className="truncate text-[12px] text-text-soft">{n.state.reason}</div>
                <div className="text-[11px] text-muted">{n.status === "dashboard_only" ? "Would send to owner" : n.status === "failed" ? "Telegram failed" : "Sent to owner"}</div>
              </div>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
