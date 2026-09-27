import { describe, expect, it } from "vitest";
import { fitContain } from "./overlay";

describe("fitContain", () => {
  it("letterboxes a 4:3 frame in a 16:9 box", () => {
    const f = fitContain(640, 480, 1600, 900);
    expect(f.scale).toBeCloseTo(1.875);
    expect(f.dx).toBeCloseTo(200);
    expect(f.dy).toBeCloseTo(0);
  });
  it("pillarboxes a portrait frame", () => {
    const f = fitContain(360, 640, 800, 800);
    expect(f.scale).toBeCloseTo(1.25);
    expect(f.dx).toBeCloseTo(175);
    expect(f.dy).toBeCloseTo(0);
  });
  it("zero sizes give an identity-safe fit", () => {
    expect(fitContain(0, 0, 100, 100)).toEqual({ scale: 1, dx: 0, dy: 0 });
  });
});
