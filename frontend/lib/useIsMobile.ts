"use client";

import { useEffect, useState } from "react";

/**
 * True while the viewport is narrower than Tailwind's `sm` breakpoint (640 px). Used only where
 * CSS alone cannot express the layout (collapsed panels, disclosure state, a sheet vs inline bar):
 * everything else stays in `sm:` classes so desktop is untouched. SSR-safe: starts false and
 * corrects on mount, matching how the page renders with JS disabled.
 */
export function useIsMobile(query = "(max-width: 639px)"): boolean {
  const [isMobile, setIsMobile] = useState(
    () => typeof window !== "undefined" && typeof window.matchMedia === "function" && window.matchMedia(query).matches,
  );
  useEffect(() => {
    if (typeof window === "undefined" || typeof window.matchMedia !== "function") return;
    const mql = window.matchMedia(query);
    const onChange = () => setIsMobile(mql.matches);
    onChange();
    mql.addEventListener("change", onChange);
    return () => mql.removeEventListener("change", onChange);
  }, [query]);
  return isMobile;
}
