const pad = (n: number) => String(n).padStart(2, "0");

export function hhmm(tsSec: number): string {
  const d = new Date(tsSec * 1000);
  return `${pad(d.getHours())}:${pad(d.getMinutes())}`;
}

export function hhmmss(tsSec: number): string {
  const d = new Date(tsSec * 1000);
  return `${hhmm(tsSec)}:${pad(d.getSeconds())}`;
}

export function agoLabel(seconds: number): string {
  if (seconds < 1) return "just now";
  if (seconds < 60) return `${Math.floor(seconds)}s ago`;
  return `${Math.floor(seconds / 60)}m ago`;
}

export function durationLabel(seconds: number): string {
  const s = Math.max(0, Math.round(seconds));
  if (s < 60) return `${s} s`;
  const m = Math.floor(s / 60);
  if (m < 60) return `${m} min`;
  return `${Math.floor(m / 60)} h ${m % 60} min`;
}

export function sinceLabel(sinceSec: number, nowSec: number): string {
  return `since ${hhmm(sinceSec)} · ${durationLabel(nowSec - sinceSec)}`;
}

export function pct(x: number): string {
  return `${Math.round(Math.min(1, Math.max(0, x)) * 100)}%`;
}
