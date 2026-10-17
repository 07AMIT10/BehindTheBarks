import { describe, expect, it } from "vitest";
import { pairingOptions } from "./pairing";

describe("pairingOptions", () => {
  it("a viewer invite carries the chosen mode ceiling", () => {
    expect(pairingOptions("viewer", "privacy")).toEqual({ mode_ceiling: "privacy", label: "viewer" });
  });

  it("every viewer ceiling choice is forwarded, including the default", () => {
    expect(pairingOptions("viewer", "full")).toEqual({ mode_ceiling: "full", label: "viewer" });
    expect(pairingOptions("viewer", "limited")).toEqual({ mode_ceiling: "limited", label: "viewer" });
  });

  it("a station mint sets no ceiling: the phone streams under its own control", () => {
    expect(pairingOptions("station", "privacy")).toEqual({});
  });
});
