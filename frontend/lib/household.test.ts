import { describe, expect, it } from "vitest";
import { canLeaveRecoveryStep, validateCreateHousehold, validateRecoveryKey, welcomeVisible } from "./household";

const households = { enabled: false, auth_required: true, mode: "households" as const };
const pin = { enabled: true, auth_required: true, mode: "pin" as const };

describe("welcomeVisible", () => {
  it("households mode without a session shows the Welcome screen", () => {
    expect(welcomeVisible(households, false)).toBe(true);
    expect(welcomeVisible(households, true)).toBe(false);
  });
  it("pin mode never shows it (the PIN gate does that job)", () => {
    expect(welcomeVisible(pin, false)).toBe(false);
    expect(welcomeVisible(null, false)).toBe(false);
  });
});

describe("validateCreateHousehold", () => {
  it("accepts empty location and reasonable names", () => {
    expect(validateCreateHousehold({ dog_name: "Rex", location: "" })).toEqual({});
    expect(validateCreateHousehold({ dog_name: "Your dog", location: "Living room" })).toEqual({});
  });
  it("rejects overlong fields the way the backend does", () => {
    expect(validateCreateHousehold({ dog_name: "x".repeat(49), location: "" })).toHaveProperty("dog_name");
    expect(validateCreateHousehold({ dog_name: "Rex", location: "y".repeat(49) })).toHaveProperty("location");
  });
});

describe("validateRecoveryKey", () => {
  it("empty is an error, a key is not", () => {
    expect(validateRecoveryKey("  ")).toHaveProperty("recovery_key");
    expect(validateRecoveryKey("abc")).toEqual({});
  });
});

describe("canLeaveRecoveryStep", () => {
  it("requires the 'I saved it' checkbox", () => {
    expect(canLeaveRecoveryStep(false)).toBe(false);
    expect(canLeaveRecoveryStep(true)).toBe(true);
  });
});

import { toggleSetting } from "./household";

describe("toggleSetting (T2.9 privacy opt-in)", () => {
  it("flips one flag and leaves the other alone", () => {
    const cur = { llm_enabled: false, log_enabled: false };
    expect(toggleSetting(cur, "llm_enabled")).toEqual({ llm_enabled: true, log_enabled: false });
    expect(toggleSetting(cur, "log_enabled")).toEqual({ llm_enabled: false, log_enabled: true });
  });
  it("turns a flag back off", () => {
    expect(toggleSetting({ llm_enabled: true, log_enabled: true }, "llm_enabled"))
      .toEqual({ llm_enabled: false, log_enabled: true });
  });
  it("is a no-op when there is no row yet", () => {
    expect(toggleSetting(null, "llm_enabled")).toBeNull();
  });
});
