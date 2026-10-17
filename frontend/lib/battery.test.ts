import { describe, expect, it } from "vitest";
import {
  batteryDisplay,
  batteryLevelClass,
  isTelemetryStale,
  shouldToast,
} from "./battery";

describe("battery utilities (T6.2)", () => {
  describe("isTelemetryStale", () => {
    it("returns true for null, undefined, or zero", () => {
      expect(isTelemetryStale(null, 100)).toBe(true);
      expect(isTelemetryStale(undefined, 100)).toBe(true);
      expect(isTelemetryStale(0, 100)).toBe(true);
    });

    it("returns false within threshold", () => {
      expect(isTelemetryStale(90, 100)).toBe(false); // 10s gap <= 45s
      expect(isTelemetryStale(56, 100)).toBe(false); // 44s gap <= 45s
    });

    it("returns true past threshold", () => {
      expect(isTelemetryStale(54, 100)).toBe(true); // 46s gap > 45s
    });
  });

  describe("batteryDisplay", () => {
    it("returns ? when stale or missing", () => {
      expect(batteryDisplay(null)).toBe("?");
      expect(batteryDisplay(undefined)).toBe("?");
      expect(batteryDisplay(-1)).toBe("?");
      expect(batteryDisplay(80, true)).toBe("?");
    });

    it("returns formatted percentage when valid", () => {
      expect(batteryDisplay(85)).toBe("85%");
      expect(batteryDisplay(100)).toBe("100%");
      expect(batteryDisplay(9.4)).toBe("9%");
    });
  });

  describe("batteryLevelClass", () => {
    it("returns unknown for stale or missing", () => {
      expect(batteryLevelClass(null)).toBe("unknown");
      expect(batteryLevelClass(50, true)).toBe("unknown");
    });

    it("returns critical for <= 10%", () => {
      expect(batteryLevelClass(10)).toBe("critical");
      expect(batteryLevelClass(5)).toBe("critical");
      expect(batteryLevelClass(0)).toBe("critical");
    });

    it("returns low for 11% - 20%", () => {
      expect(batteryLevelClass(11)).toBe("low");
      expect(batteryLevelClass(20)).toBe("low");
    });

    it("returns normal for > 20%", () => {
      expect(batteryLevelClass(21)).toBe("normal");
      expect(batteryLevelClass(95)).toBe("normal");
    });
  });

  describe("shouldToast", () => {
    it("returns null when charging", () => {
      expect(shouldToast(25, 20, true)).toBeNull();
      expect(shouldToast(15, 10, true)).toBeNull();
      expect(shouldToast(null, 5, true)).toBeNull();
    });

    it("returns null when next is invalid", () => {
      expect(shouldToast(25, null, false)).toBeNull();
      expect(shouldToast(25, undefined, false)).toBeNull();
    });

    it("alerts on initial low or critical reading when not charging", () => {
      expect(shouldToast(null, 20, false)).toBe("low");
      expect(shouldToast(null, 10, false)).toBe("critical");
      expect(shouldToast(null, 50, false)).toBeNull();
    });

    it("alerts on downward crossing to low (<=20% and >10%)", () => {
      expect(shouldToast(25, 20, false)).toBe("low");
      expect(shouldToast(21, 19, false)).toBe("low");
      // staying within low does not re-alert
      expect(shouldToast(19, 18, false)).toBeNull();
    });

    it("alerts on downward crossing to critical (<=10%)", () => {
      expect(shouldToast(15, 10, false)).toBe("critical");
      expect(shouldToast(11, 8, false)).toBe("critical");
      // staying within critical does not re-alert
      expect(shouldToast(8, 7, false)).toBeNull();
    });

    it("does not alert when battery level increases", () => {
      expect(shouldToast(10, 15, false)).toBeNull();
      expect(shouldToast(18, 22, false)).toBeNull();
    });
  });
});
