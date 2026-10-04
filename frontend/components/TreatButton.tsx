type Props = { onTreat: () => void; flash?: boolean };

export default function TreatButton({ onTreat, flash = false }: Props) {
  return (
    <button type="button" onClick={onTreat}
      className={`relative flex h-[60px] w-full items-center justify-center gap-3 rounded-lg text-[17px] font-bold transition-all active:scale-[0.98] ${
        flash
          ? "bg-[#00E676] text-black shadow-[0_0_20px_rgba(0,230,118,0.4)]"
          : "bg-accent text-accent-fg hover:brightness-105"
      }`}>
      {flash ? (
        <svg width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.4" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true"><path d="M5 12.5l4.5 4.5L19 7.5" /></svg>
      ) : (
        <svg width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.9" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true"><path d="M3 13h18a9 5 0 0 1-18 0z" /><path d="M12 3v6" /><path d="M9 6.5l3 3 3-3" /></svg>
      )}
      <span>{flash ? "Treat dropped · Chime sent 🔔" : "Drop Treat"}</span>
      <span className="hidden rounded-[4px] border border-current px-1.5 py-0.5 font-mono text-[12px] font-medium opacity-70 lg:inline">T</span>
    </button>
  );
}
