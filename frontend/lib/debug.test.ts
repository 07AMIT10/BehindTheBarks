import { describe, expect, it } from "vitest";
import {
  downsampleSeries,
  extractSeriesValues,
  seriesToSparkline,
  type TelemetrySample,
} from "./debug";

describe("debug helpers (T6.3)", () => {
  describe("seriesToSparkline", () => {
    it("returns empty string for empty input", () => {
      expect(seriesToSparkline([])).toBe("");
    });

    it("returns horizontal line for single point", () => {
      expect(seriesToSparkline([50])).toBe("0,12 100,12");
    });

    it("scales two points properly with inverted Y", () => {
      // min = 10, max = 20. Point 1 (10) should be near bottom (22), Point 2 (20) near top (2)
      const line = seriesToSparkline([10, 20]);
      expect(line).toBe("0,22 100,2");
    });

    it("handles flat values without division by zero", () => {
      const line = seriesToSparkline([5, 5, 5]);
      expect(line).toContain("0,");
      expect(line).toContain("100,");
    });
  });

  describe("downsampleSeries", () => {
    it("returns original items when count <= maxPoints", () => {
      const items = [1, 2, 3];
      expect(downsampleSeries(items, 5)).toEqual([1, 2, 3]);
    });

    it("downsamples larger arrays to exact maxPoints preserving endpoints", () => {
      const items = Array.from({ length: 100 }, (_, i) => i);
      const sampled = downsampleSeries(items, 10);
      expect(sampled).toHaveLength(10);
      expect(sampled[0]).toBe(0);
      expect(sampled[sampled.length - 1]).toBe(99);
    });

    it("handles maxPoints 1", () => {
      expect(downsampleSeries([1, 2, 3], 1)).toEqual([3]);
    });
  });

  describe("extractSeriesValues", () => {
    it("filters out null and undefined values", () => {
      const series: TelemetrySample[] = [
        { ts: 1, battery_pct: 80 },
        { ts: 2, battery_pct: undefined },
        { ts: 3, battery_pct: 79 },
      ];
      const values = extractSeriesValues(series, (s) => s.battery_pct);
      expect(values).toEqual([80, 79]);
    });
  });
});
