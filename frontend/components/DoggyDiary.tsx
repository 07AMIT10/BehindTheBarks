"use client";

import { useCallback, useEffect, useState } from "react";

export type DiaryHighlight = {
  kind: "rest" | "play" | "alert" | "routine";
  title: string;
  time: string;
  duration_mins: number;
  reason: string;
  snapshot?: string | null;
};

export type DiaryData = {
  ok: boolean;
  date: string;
  dog_name: string;
  happiness_score: number;
  mood_label: string;
  total_monitored_minutes: number;
  summary: string;
  buckets: {
    rest_minutes: number;
    play_minutes: number;
    alert_minutes: number;
    routine_minutes: number;
  };
  percentages: {
    rest: number;
    play: number;
    alert: number;
    routine: number;
  };
  highlights: DiaryHighlight[];
};

type Props = {
  stationId?: string;
  onViewSnapshot?: (snapshot: string, title: string) => void;
};

export default function DoggyDiary({ stationId, onViewSnapshot }: Props) {
  const [diary, setDiary] = useState<DiaryData | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const fetchDiary = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const qs = stationId && stationId !== "default" ? `?station=${encodeURIComponent(stationId)}` : "";
      const res = await fetch(`/diary/daily${qs}`);
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const data: DiaryData = await res.json();
      setDiary(data);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Failed to load diary");
    } finally {
      setLoading(false);
    }
  }, [stationId]);

  useEffect(() => {
    void fetchDiary();
  }, [fetchDiary]);

  if (error && !diary) {
    return (
      <div className="rounded-xl border border-border bg-surface p-4 text-center">
        <p className="text-small text-muted mb-2">Could not load today&rsquo;s Doggy Diary.</p>
        <button
          type="button"
          onClick={fetchDiary}
          className="text-xs font-semibold text-accent hover:underline cursor-pointer"
        >
          Retry
        </button>
      </div>
    );
  }

  if (!diary && loading) {
    return (
      <div className="rounded-xl border border-border bg-surface p-6 text-center text-muted text-small animate-pulse">
        🐾 Generating today&rsquo;s Doggy Diary…
      </div>
    );
  }

  if (!diary) return null;

  const score = diary.happiness_score;
  const scoreColor =
    score >= 80 ? "text-emerald-400 border-emerald-500/40 bg-emerald-500/10" :
    score >= 60 ? "text-teal-400 border-teal-500/40 bg-teal-500/10" :
    score >= 45 ? "text-amber-400 border-amber-500/40 bg-amber-500/10" :
    "text-rose-400 border-rose-500/40 bg-rose-500/10";

  return (
    <section aria-label="Daily Doggy Diary" className="flex flex-col gap-4 rounded-xl border border-border bg-surface px-4 py-5 lg:px-6">
      {/* Top Header */}
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div className="flex items-center gap-2">
          <span className="text-lg">📔</span>
          <h2 className="text-heading font-semibold text-text">
            {diary.dog_name}&rsquo;s Doggy Diary
          </h2>
          <span className="text-[12px] font-mono text-muted">· {diary.date}</span>
        </div>
        <button
          type="button"
          disabled={loading}
          onClick={fetchDiary}
          title="Refresh diary"
          className="flex items-center gap-1.5 rounded-lg border border-border px-2.5 py-1 text-[12px] font-semibold text-muted hover:text-text hover:bg-surface-2 transition-colors cursor-pointer"
        >
          <svg className={loading ? "animate-spin" : ""} width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5"><path d="M21.5 2v6h-6M21.34 15.57a10 10 0 1 1-.57-8.38l5.67-5.67"/></svg>
          <span>{loading ? "Updating…" : "Refresh"}</span>
        </button>
      </div>

      {/* Hero Wellbeing Score & Narrative */}
      <div className="grid grid-cols-1 md:grid-cols-[140px_minmax(0,1fr)] gap-4 items-center rounded-xl bg-surface-2/60 p-4 border border-border/60">
        <div className="flex flex-col items-center justify-center text-center">
          <div className={`relative flex h-24 w-24 items-center justify-center rounded-full border-4 ${scoreColor} shadow-inner`}>
            <div className="flex flex-col items-center">
              <span className="text-2xl font-black tracking-tight">{score}%</span>
              <span className="text-[10px] font-bold uppercase tracking-wider opacity-80">Happy</span>
            </div>
          </div>
          <span className="mt-2 text-xs font-bold text-text-soft">{diary.mood_label}</span>
        </div>
        <div className="flex flex-col justify-center gap-1.5">
          <p className="text-body font-medium text-text">{diary.summary}</p>
          <div className="flex flex-wrap items-center gap-3 text-[12px] text-muted font-mono mt-1">
            <span>⏱️ {diary.total_monitored_minutes}m monitored</span>
            <span>·</span>
            <span>💤 {diary.buckets.rest_minutes}m rest</span>
            <span>·</span>
            <span>🎾 {diary.buckets.play_minutes}m active</span>
            {diary.buckets.routine_minutes > 0 && (
              <>
                <span>·</span>
                <span>🍖 {diary.buckets.routine_minutes}m treats</span>
              </>
            )}
          </div>
        </div>
      </div>

      {/* Activity Breakdown Progress Bar */}
      <div className="flex flex-col gap-1.5">
        <div className="flex h-3 w-full overflow-hidden rounded-full bg-track">
          <div
            title={`Rest: ${diary.percentages.rest}%`}
            style={{ width: `${diary.percentages.rest}%` }}
            className="h-full bg-indigo-500 transition-all duration-500"
          />
          <div
            title={`Play: ${diary.percentages.play}%`}
            style={{ width: `${diary.percentages.play}%` }}
            className="h-full bg-emerald-500 transition-all duration-500"
          />
          <div
            title={`Alert: ${diary.percentages.alert}%`}
            style={{ width: `${diary.percentages.alert}%` }}
            className="h-full bg-rose-500 transition-all duration-500"
          />
          <div
            title={`Routine: ${diary.percentages.routine}%`}
            style={{ width: `${diary.percentages.routine}%` }}
            className="h-full bg-amber-500 transition-all duration-500"
          />
        </div>
        <div className="flex flex-wrap justify-between gap-2 text-[12px] text-muted font-mono pt-1">
          <span className="flex items-center gap-1.5">
            <span className="h-2 w-2 rounded-full bg-indigo-500 inline-block" />
            <span>Rest {diary.buckets.rest_minutes}m ({diary.percentages.rest}%)</span>
          </span>
          <span className="flex items-center gap-1.5">
            <span className="h-2 w-2 rounded-full bg-emerald-500 inline-block" />
            <span>Play {diary.buckets.play_minutes}m ({diary.percentages.play}%)</span>
          </span>
          <span className="flex items-center gap-1.5">
            <span className="h-2 w-2 rounded-full bg-rose-500 inline-block" />
            <span>Alert {diary.buckets.alert_minutes}m ({diary.percentages.alert}%)</span>
          </span>
          <span className="flex items-center gap-1.5">
            <span className="h-2 w-2 rounded-full bg-amber-500 inline-block" />
            <span>Routine {diary.buckets.routine_minutes}m ({diary.percentages.routine}%)</span>
          </span>
        </div>
      </div>

      {/* Notable Highlights */}
      {diary.highlights.length > 0 && (
        <div className="flex flex-col gap-2 pt-2 border-t border-border/60">
          <h3 className="text-[13px] font-semibold uppercase tracking-wide text-muted">Today&rsquo;s Highlights</h3>
          <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-2.5">
            {diary.highlights.map((h, i) => {
              const icon = h.kind === "rest" ? "💤" : h.kind === "play" ? "🎾" : h.kind === "alert" ? "🚨" : "🍖";
              return (
                <div key={i} className="flex flex-col justify-between rounded-lg bg-surface-2 p-3 border border-border/70 hover:border-border transition-colors">
                  <div className="flex items-center justify-between gap-2 mb-1.5">
                    <span className="flex items-center gap-1.5 font-semibold text-text text-small">
                      <span>{icon}</span>
                      <span>{h.title}</span>
                    </span>
                    <span className="font-mono text-[11px] text-muted bg-surface px-1.5 py-0.5 rounded">
                      {h.time} · {h.duration_mins}m
                    </span>
                  </div>
                  <p className="text-[12px] text-text-soft line-clamp-2">{h.reason}</p>
                  {h.snapshot && (
                    <button
                      type="button"
                      onClick={() => onViewSnapshot?.(h.snapshot!, h.title)}
                      className="mt-2 text-left text-xs font-semibold text-accent hover:underline flex items-center gap-1 cursor-pointer"
                    >
                      <svg width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2"><circle cx="12" cy="12" r="10"/><circle cx="12" cy="12" r="3"/></svg>
                      <span>View moment photo</span>
                    </button>
                  )}
                </div>
              );
            })}
          </div>
        </div>
      )}
    </section>
  );
}
