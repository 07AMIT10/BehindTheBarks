"use client";

import { type RefObject, useEffect, useRef } from "react";
import { FOCUSABLE, nextFocusIndex } from "./focusTrap";

/**
 * Modal behaviour for a dialog root: focus moves in on open and returns to the opener on close,
 * Tab cycles inside the dialog, and Escape calls onClose. The root needs tabIndex={-1}.
 */
export function useDialog(ref: RefObject<HTMLElement | null>, open: boolean, onClose: () => void): void {
  const closeRef = useRef(onClose);
  useEffect(() => {
    closeRef.current = onClose;
  });

  useEffect(() => {
    const root = ref.current;
    if (!open || !root) return;
    const opener = document.activeElement as HTMLElement | null;
    const focusables = () =>
      [...root.querySelectorAll<HTMLElement>(FOCUSABLE)].filter((el) => el.getClientRects().length > 0);

    const first = focusables()[0];
    (first ?? root).focus({ preventScroll: true });

    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") {
        e.preventDefault();
        closeRef.current();
        return;
      }
      if (e.key !== "Tab") return;
      const list = focusables();
      const idx = list.indexOf(document.activeElement as HTMLElement);
      const next = nextFocusIndex(idx, list.length, e.shiftKey);
      if (next === null) return;
      e.preventDefault();
      if (list.length) list[next].focus();
      else root.focus();
    };
    document.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("keydown", onKey);
      if (opener && typeof opener.focus === "function" && opener.isConnected) opener.focus({ preventScroll: true });
    };
  }, [ref, open]);
}
