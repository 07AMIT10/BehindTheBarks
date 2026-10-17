import { describe, expect, it } from "vitest";
import { nextFocusIndex } from "./focusTrap";

describe("nextFocusIndex", () => {
  it("lets the browser move focus between inner elements", () => {
    expect(nextFocusIndex(0, 3, false)).toBeNull();
    expect(nextFocusIndex(1, 3, false)).toBeNull();
    expect(nextFocusIndex(2, 3, true)).toBeNull();
  });
  it("wraps from the last element forward and from the first backward", () => {
    expect(nextFocusIndex(2, 3, false)).toBe(0);
    expect(nextFocusIndex(0, 3, true)).toBe(2);
  });
  it("pulls focus back in when it is outside the dialog", () => {
    expect(nextFocusIndex(-1, 3, false)).toBe(0);
    expect(nextFocusIndex(-1, 3, true)).toBe(2);
  });
  it("does nothing when the dialog has no focusable element", () => {
    expect(nextFocusIndex(-1, 0, false)).toBeNull();
  });
});
