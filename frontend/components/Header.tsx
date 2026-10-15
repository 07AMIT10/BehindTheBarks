type Props = { dogName: string; location: string; children?: React.ReactNode };

function Logo({ size }: { size: number }) {
  return (
    <svg width={size} height={size} viewBox="0 0 32 32" fill="none" aria-hidden="true">
      <rect x="1" y="1" width="30" height="30" rx="9" stroke="var(--accent)" strokeWidth="1.5" className="fill-accent/10" />
      <g transform="translate(16 16) scale(0.025) translate(-512 -512)">
        <path
          d="M 300,620 C 330,510 450,480 512,490 C 574,480 694,510 724,620 C 734,690 684,755 630,755 C 575,755 545,715 512,715 C 479,715 449,755 394,755 C 340,755 290,690 300,620 Z"
          fill="var(--accent)"
        />
        <ellipse cx="245" cy="410" rx="62" ry="95" transform="rotate(-30 245 410)" fill="var(--accent)" />
        <ellipse cx="410" cy="280" rx="70" ry="115" transform="rotate(-10 410 280)" fill="var(--accent)" />
        <ellipse cx="614" cy="280" rx="70" ry="115" transform="rotate(10 614 280)" fill="var(--accent)" />
        <ellipse cx="779" cy="410" rx="62" ry="95" transform="rotate(30 779 410)" fill="var(--accent)" />
      </g>
    </svg>
  );
}

export default function Header({ dogName, location, children }: Props) {
  return (
    <header className="flex h-[52px] items-center gap-2 lg:h-12 lg:gap-6">
      <div className="hidden items-center gap-3 lg:flex">
        <Logo size={32} />
        <div className="flex flex-col">
          <div className="text-[19px] font-bold tracking-[-0.01em] leading-tight text-text">WagWatch</div>
          <div className="text-[12px] font-medium text-accent tracking-wide uppercase">Behind The Barks</div>
        </div>
      </div>
      <div className="hidden h-7 w-px bg-border lg:block" />
      <div className="flex items-center gap-2 lg:hidden"><Logo size={28} /></div>
      <div className="flex min-w-[6.5rem] flex-1 flex-col gap-0.5 lg:min-w-0 lg:flex-none">
        <div className="truncate text-heading font-bold lg:text-body lg:font-semibold text-text">{dogName}</div>
        <div className="truncate text-[12px] text-muted lg:text-small">
          {location ? (
            <>
              <span className="lg:hidden">WagWatch · {location}</span>
              <span className="hidden lg:inline">Feeding area · {location}</span>
            </>
          ) : (
            <span className="lg:hidden">WagWatch</span>
          )}
        </div>
      </div>
      <div className="grow" />
      <div className="flex max-w-[55%] items-center gap-1.5 overflow-x-auto p-1 [&>*]:shrink-0 lg:max-w-none lg:gap-2 lg:overflow-visible lg:p-0">{children}</div>
    </header>
  );
}
