type Props = { onGoLive: () => void };

export default function DemoBanner({ onGoLive }: Props) {
  return (
    <div role="status" className="flex flex-wrap items-center gap-x-3 gap-y-1 rounded-md px-4 py-2.5 text-small"
      style={{ background: "var(--accent-soft)", color: "var(--accent-ink)" }}>
      <svg width="16" height="16" viewBox="0 0 24 24" fill="currentColor" aria-hidden="true"><path d="M7 4.5v15l13-7.5z" /></svg>
      <span className="font-bold">Demo mode</span>
      <span>Playing a recorded clip, not the live camera</span>
      <span className="grow" />
      <button type="button" onClick={onGoLive}
        className="h-9 rounded-md border bg-white px-3 text-[13px] font-semibold"
        style={{ borderColor: "var(--accent)", color: "var(--accent-ink)" }}>
        Go live
      </button>
    </div>
  );
}
