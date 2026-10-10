import { describe, expect, it } from "vitest";
import { MODE_METADATA, MODE_ORDER, isModeAllowed, minMode, modeRank, modeResultLabel } from "./modes";

describe("modes library (T3.6)", () => {
  it("enforces strict mode ordering", () => {
    expect(MODE_ORDER).toEqual(["blackout", "privacy", "limited", "full"]);
    expect(modeRank("blackout")).toBe(0);
    expect(modeRank("privacy")).toBe(1);
    expect(modeRank("limited")).toBe(2);
    expect(modeRank("full")).toBe(3);
  });

  it("minMode computes the minimum mode correctly", () => {
    expect(minMode("full", "limited")).toBe("limited");
    expect(minMode("full", "blackout", "privacy")).toBe("blackout");
    expect(minMode("privacy", "limited")).toBe("privacy");
    expect(minMode("full")).toBe("full");
    expect(minMode()).toBe("full");
  });

  it("exposes all labels and descriptions in MODE_METADATA", () => {
    for (const m of MODE_ORDER) {
      const meta = MODE_METADATA[m];
      expect(meta.label).toBeDefined();
      expect(meta.desc).toBeDefined();
      expect(meta.icon).toBeDefined();
    }
    expect(MODE_METADATA.blackout.label).toBe("Blackout");
    expect(MODE_METADATA.privacy.label).toBe("Privacy");
    expect(MODE_METADATA.limited.label).toBe("Limited");
    expect(MODE_METADATA.full.label).toBe("Full");
  });

  it("checks whether options are allowed against ceilings", () => {
    expect(isModeAllowed("full", "full")).toBe(true);
    expect(isModeAllowed("full", "limited")).toBe(false);
    expect(isModeAllowed("limited", "limited")).toBe(true);
    expect(isModeAllowed("privacy", "limited")).toBe(true);
    expect(isModeAllowed("blackout", "limited")).toBe(true);

    expect(isModeAllowed("full", "privacy")).toBe(false);
    expect(isModeAllowed("limited", "privacy")).toBe(false);
    expect(isModeAllowed("privacy", "privacy")).toBe(true);
    expect(isModeAllowed("blackout", "privacy")).toBe(true);

    expect(isModeAllowed("full", "blackout")).toBe(false);
    expect(isModeAllowed("privacy", "blackout")).toBe(false);
    expect(isModeAllowed("blackout", "blackout")).toBe(true);
  });
});

describe("modeResultLabel (mode_result from the phone)", () => {
  it("names each outcome the phone can report", () => {
    expect(modeResultLabel("allowed")).toBe("Request approved on the phone");
    expect(modeResultLabel("denied")).toBe("Request declined on the phone");
    expect(modeResultLabel("timeout")).toBe("No answer on the phone");
  });

  it("gives three distinct labels", () => {
    const labels = new Set((["allowed", "denied", "timeout"] as const).map(modeResultLabel));
    expect(labels.size).toBe(3);
  });
});
