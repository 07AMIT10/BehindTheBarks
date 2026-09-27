import type { Emotion } from "./contracts";
import { NEGATIVE } from "./emotions";
import type { NotificationItem } from "./types";

export const FADE_MS = 6000;
export const GROUP_MS = 120000;
export const MAX_TOASTS = 4;

export type Toast = {
  id: string;
  kind: "negative" | "positive" | "system";
  emotion?: Emotion;
  title: string;
  body: string;
  ts: number;
  status?: NotificationItem["status"];
  detail?: string;
  count: number;
  sticky: boolean;
  createdAt: number;
};

let counter = 0;
const nextId = () => `t${Date.now().toString(36)}${(counter++).toString(36)}`;

export function toastFromNotification(list: Toast[], item: NotificationItem, dogName: string, nowMs: number): Toast[] {
  const e = item.state.emotion;
  const negative = NEGATIVE.has(e);
  const existing = list.find((t) => t.emotion === e && t.kind !== "system" && nowMs - t.createdAt < GROUP_MS);
  const base: Toast = {
    id: existing?.id ?? nextId(),
    kind: negative ? "negative" : "positive",
    emotion: e,
    title: `${dogName} ${negative ? "seems" : "is"} ${e}`,
    body: item.state.reason,
    ts: item.state.ts,
    status: item.status,
    detail: item.detail,
    count: existing ? existing.count + 1 : 1,
    sticky: negative,
    createdAt: existing ? existing.createdAt : nowMs,
  };
  const rest = list.filter((t) => t.id !== base.id);
  return [base, ...rest].slice(0, MAX_TOASTS);
}

export function systemToast(list: Toast[], title: string, nowMs: number): Toast[] {
  const t: Toast = { id: nextId(), kind: "system", title, body: "", ts: nowMs / 1000, count: 1, sticky: false, createdAt: nowMs };
  return [t, ...list].slice(0, MAX_TOASTS);
}

export function expireToasts(list: Toast[], nowMs: number): Toast[] {
  const next = list.filter((t) => t.sticky || nowMs - t.createdAt <= FADE_MS);
  return next.length === list.length ? list : next;
}

export function dismissToast(list: Toast[], id: string): Toast[] {
  return list.filter((t) => t.id !== id);
}
