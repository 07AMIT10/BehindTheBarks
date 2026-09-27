import { describe, expect, it } from "vitest";
import { deviceLabel, phoneNotice } from "./phone";
import type { PhoneStatus, Status } from "./types";

const phone = (p: Partial<PhoneStatus>): Status => ({
  pipeline: "mock",
  pipeline_status: { source: "browser", state: "running", fps: 8, last_frame_age_s: 0.1, audio_ok: true },
  phone: { connected: true, device: "Pixel 7", facing: "back", camera: true, fps: 8, ...p },
});

describe("phoneNotice", () => {
  it("nothing without a phone or while streaming normally", () => {
    expect(phoneNotice(null)).toBeNull();
    expect(phoneNotice({ pipeline: "mock", phone: null })).toBeNull();
    expect(phoneNotice(phone({}))).toBeNull();
  });
  it("camera denied on the phone", () => {
    expect(phoneNotice(phone({ camera: false }))).toBe("blocked");
  });
  it("phone dropped mid-session", () => {
    expect(phoneNotice(phone({ connected: false }))).toBe("dropped");
    expect(phoneNotice(phone({ connected: false, camera: false }))).toBe("dropped");
  });
});

describe("deviceLabel", () => {
  it("connected phone, mic-only phone, dropped phone", () => {
    expect(deviceLabel(phone({}))).toBe("Pixel 7 · back camera");
    expect(deviceLabel(phone({ facing: "front" }))).toBe("Pixel 7 · front camera");
    expect(deviceLabel(phone({ camera: false }))).toBe("Pixel 7 · microphone only");
    expect(deviceLabel(phone({ connected: false }))).toBe("Pixel 7 · disconnected");
  });
  it("falls back to the pipeline source", () => {
    expect(deviceLabel({ pipeline_status: { source: "mock", state: "running", fps: 8, last_frame_age_s: 0, audio_ok: false } })).toBe("Mock camera");
    expect(deviceLabel({ pipeline_status: { source: "browser", state: "stalled", fps: 8, last_frame_age_s: 3, audio_ok: false } })).toBe("Phone camera · waiting");
    expect(deviceLabel(null)).toBe("No camera yet");
  });
});
