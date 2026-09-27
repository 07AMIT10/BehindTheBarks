type Props = { dogName: string; location: string; children?: React.ReactNode };

function Logo({ size }: { size: number }) {
  return (
    <svg width={size} height={size} viewBox="0 0 32 32" fill="none" aria-hidden="true">
      <rect x="1" y="1" width="30" height="30" rx="9" stroke="var(--accent)" strokeWidth="2" />
      <path d="M7 17h4l2.5-6 4 11 2.5-5H25" stroke="var(--accent)" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" />
    </svg>
  );
}

export default function Header({ dogName, location, children }: Props) {
  return (
    <header className="flex h-[52px] items-center gap-3 lg:h-12 lg:gap-6">
      <div className="hidden items-center gap-3 lg:flex">
        <Logo size={32} />
        <div className="text-[20px] font-bold tracking-[-0.01em]">Claude Pet</div>
      </div>
      <div className="hidden h-7 w-px bg-border lg:block" />
      <div className="flex items-center gap-3 lg:hidden"><Logo size={28} /></div>
      <div className="flex min-w-0 flex-col gap-0.5">
        <div className="truncate text-heading font-bold lg:text-body lg:font-semibold">{dogName}</div>
        <div className="truncate text-[12px] text-muted lg:text-small">
          <span className="lg:hidden">Claude Pet · {location}</span>
          <span className="hidden lg:inline">Feeding area · {location}</span>
        </div>
      </div>
      <div className="grow" />
      <div className="flex items-center gap-2">{children}</div>
    </header>
  );
}
