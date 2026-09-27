export default function Sparkline({ points }: { points: string }) {
  return (
    <svg viewBox="0 0 100 24" preserveAspectRatio="none" className="h-6 w-full" aria-hidden="true">
      {points && (
        <polyline points={points} fill="none" stroke="var(--accent)" strokeWidth="1.75"
          vectorEffect="non-scaling-stroke" strokeLinejoin="round" />
      )}
    </svg>
  );
}
