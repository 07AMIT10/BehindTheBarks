import { describe, expect, it } from "vitest";
import { agoLabel, durationLabel, hhmm, hhmmss, pct, sinceLabel } from "./format";

const at = (h: number, m: number, s = 0) => new Date(2026, 8, 27, h, m, s).getTime() / 1000;

describe("format", () => {
  it("clock strings are local time, zero padded", () => {
    expect(hhmm(at(8, 5))).toBe("08:05");
    expect(hhmmss(at(18, 30, 4))).toBe("18:30:04");
  });
  it("ago label", () => {
    expect(agoLabel(0.4)).toBe("just now");
    expect(agoLabel(3.2)).toBe("3s ago");
    expect(agoLabel(125)).toBe("2m ago");
  });
  it("duration label", () => {
    expect(durationLabel(45)).toBe("45 s");
    expect(durationLabel(180)).toBe("3 min");
    expect(durationLabel(3900)).toBe("1 h 5 min");
  });
  it("since label", () => {
    expect(sinceLabel(at(18, 27), at(18, 30))).toBe("since 18:27 · 3 min");
  });
  it("pct rounds and clamps", () => {
    expect(pct(0.864)).toBe("86%");
    expect(pct(1.3)).toBe("100%");
  });
});
