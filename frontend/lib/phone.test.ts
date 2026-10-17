import { describe, expect, it } from "vitest";
import { deviceLabel, PAUSED_NOTICE, phoneNotice, shouldStream, stationState } from "./phone";
import type { PhoneStatus, Status } from "./types";

const phone = (p: Partial<PhoneStatus>): Status => ({
  pipeline: "mock",
  pipeline_status: { source: "browser", state: "running", fps: 8, last_frame_age_s: 0.1, audio_ok: true },
  phone: { connected: true, device: "Pixel 7", facing: "back", camera: true, fps: 8, ...p },
});

const paused = (extra: Partial<PhoneStatus> = {}): Status => ({
  ...phone(extra),
  pipeline_status: { source: "remote", state: "paused", fps: 0, last_frame_age_s: 40, audio_ok: false },
});

const idleRemote: Status = {
  pipeline: "remote",
  pipeline_status: { source: "remote", state: "idle", fps: 0, last_frame_age_s: null, audio_ok: false },
  phone: null,
};

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
  it("idle pipeline with no phone says nothing is streaming", () => {
    expect(deviceLabel(idleRemote)).toBe("No camera streaming");
  });
});

describe("stationState", () => {
  it("idle after a deliberate stop or before the first phone ever", () => {
    expect(stationState(idleRemote)).toBe("idle");
    expect(stationState({ ...idleRemote, phone: null })).toBe("idle");
  });
  it("live while a mock/browser source runs with no phone", () => {
    expect(stationState({ pipeline_status: { source: "mock", state: "running", fps: 8, last_frame_age_s: 0, audio_ok: false } })).toBe("live");
  });
  it("stalled is its own state, not idle", () => {
    expect(stationState({ ...idleRemote, pipeline_status: { source: "remote", state: "stalled", fps: 0, last_frame_age_s: 5, audio_ok: false } })).toBe("stalled");
  });
  it("a deliberate blackout is paused: neutral, never stalled or dropped", () => {
    expect(stationState(paused())).toBe("paused");
    expect(stationState({ ...idleRemote, pipeline_status: { ...idleRemote.pipeline_status!, state: "paused" } })).toBe("paused");
    // a phone that really dropped is still dropped, whatever the pipeline says
    expect(stationState(paused({ connected: false }))).toBe("dropped");
    expect(stationState(paused({ camera: false }))).toBe("blocked");
  });
  it("the status bar names a paused station as paused, not as a reconnect", () => {
    expect(deviceLabel(paused())).toBe("Pixel 7 · paused (Blackout)");
    expect(PAUSED_NOTICE).toBe("Sharing is paused — the station is in Blackout. Nothing leaves the phone.");
  });
  it("a connected phone is live, camera-denied is blocked, dropped is dropped", () => {
    expect(stationState(phone({}))).toBe("live");
    expect(stationState(phone({ camera: false }))).toBe("blocked");
    expect(stationState(phone({ connected: false }))).toBe("dropped");
  });
});

describe("shouldStream", () => {
  it("keeps the MJPEG stream for a live, stalled (frozen frame) or mic-only station", () => {
    expect(shouldStream("live")).toBe(true);
    expect(shouldStream("stalled")).toBe(true);
    expect(shouldStream("blocked")).toBe(true);
  });
  it("opens no stream for idle, paused (Blackout) or dropped phones", () => {
    expect(shouldStream("idle")).toBe(false);
    expect(shouldStream("paused")).toBe(false);
    expect(shouldStream("dropped")).toBe(false);
  });
  it("agrees with stationState for a dropped phone", () => {
    expect(shouldStream(stationState(phone({ connected: false })))).toBe(false);
    expect(shouldStream(stationState(phone({})))).toBe(true);
  });
});
