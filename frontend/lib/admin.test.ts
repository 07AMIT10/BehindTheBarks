import { describe, expect, it } from "vitest";
import { adminErrorMessage, adminHeaders, changedTuning, isTuning, validateTuning, type Tuning } from "./admin";

const current: Tuning = { video_fps: 4, ws_frame_fps: 4, gzip_enabled: true, gzip_min_size: 1000 };

describe("validateTuning", () => {
  it("accepts values inside the server's bounds", () => {
    expect(validateTuning({ video_fps: 0.5, ws_frame_fps: 30, gzip_min_size: 0, gzip_enabled: false })).toEqual({});
  });

  it("names the field and the bounds when a value is out of range", () => {
    const errors = validateTuning({ video_fps: 0, ws_frame_fps: 31, gzip_min_size: 2_000_000 });
    expect(errors.video_fps).toBe("Must be between 0.5 and 30");
    expect(errors.ws_frame_fps).toBe("Must be between 0.5 and 30");
    expect(errors.gzip_min_size).toBe("Must be between 0 and 1000000");
  });

  it("rejects NaN from an empty or non-numeric field", () => {
    expect(validateTuning({ video_fps: Number.NaN }).video_fps).toBe("Enter a number");
  });

  it("only checks the keys it was given", () => {
    expect(validateTuning({ gzip_enabled: true })).toEqual({});
  });
});

describe("changedTuning", () => {
  it("sends only the keys that changed", () => {
    expect(changedTuning(current, { ...current, video_fps: 2 })).toEqual({ video_fps: 2 });
  });

  it("is empty when nothing changed", () => {
    expect(changedTuning(current, { ...current })).toEqual({});
  });

  it("includes a toggled boolean, even when it is false", () => {
    expect(changedTuning(current, { ...current, gzip_enabled: false })).toEqual({ gzip_enabled: false });
  });
});

describe("adminErrorMessage", () => {
  it("explains a rejected token", () => {
    expect(adminErrorMessage(401, {})).toBe("The admin token was not accepted.");
  });

  it("explains a disabled admin API using the server's detail", () => {
    expect(adminErrorMessage(403, { detail: "admin API disabled: set BTB_API_TOKEN" })).toContain("BTB_API_TOKEN");
  });

  it("passes through the server's reason for a 400", () => {
    expect(adminErrorMessage(400, { detail: "video_fps must be <= 30.0" })).toBe("video_fps must be <= 30.0");
  });

  it("falls back to the status code", () => {
    expect(adminErrorMessage(502, null)).toBe("Request failed (HTTP 502).");
  });
});

describe("isTuning", () => {
  it("accepts the server shape and rejects anything else", () => {
    expect(isTuning(current)).toBe(true);
    expect(isTuning({ ...current, gzip_enabled: "yes" })).toBe(false);
    expect(isTuning(null)).toBe(false);
  });
});

describe("adminHeaders", () => {
  it("adds a Bearer header for a token and trims it", () => {
    expect(adminHeaders("  abc ")).toEqual({ Authorization: "Bearer abc", "Content-Type": "application/json" });
  });

  it("sends no Authorization header when the token is empty", () => {
    expect(adminHeaders("   ")).toEqual({ "Content-Type": "application/json" });
  });
});
