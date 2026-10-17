// Pure focus-trap arithmetic for modal dialogs (used by useDialog).

export const FOCUSABLE =
  'a[href], button:not([disabled]), input:not([disabled]):not([type="hidden"]), select:not([disabled]), ' +
  'textarea:not([disabled]), [tabindex]:not([tabindex="-1"])';

/**
 * Where Tab (or Shift+Tab) should land inside a dialog with `count` focusable elements, given the
 * index of the currently focused one (-1 when focus is outside the dialog). Returns null when the
 * browser's own Tab order already stays inside, so the caller leaves the key alone.
 */
export function nextFocusIndex(current: number, count: number, backwards: boolean): number | null {
  if (count <= 0) return null;
  if (current < 0 || current >= count) return backwards ? count - 1 : 0;
  if (backwards && current === 0) return count - 1;
  if (!backwards && current === count - 1) return 0;
  return null;
}
