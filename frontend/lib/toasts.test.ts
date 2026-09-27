import { describe, expect, it } from "vitest";
import { dismissToast, expireToasts, FADE_MS, GROUP_MS, MAX_TOASTS, systemToast, toastFromNotification } from "./toasts";
import type { NotificationItem } from "./types";

const N = (id: number, emotion: NotificationItem["state"]["emotion"], reason = "r"): NotificationItem => ({
  id, ts: 100 + id, read: false, status: "dashboard_only", detail: "would send to owner",
  state: { ts: 100 + id, emotion, confidence: 0.8, source: "fused", reason, snapshot: null },
});

describe("toasts", () => {
  it("negative is sticky with 'seems', positive fades with 'is'", () => {
    let t = toastFromNotification([], N(1, "anxious"), "Bruno", 0);
    t = toastFromNotification(t, N(2, "excited"), "Bruno", 0);
    expect(t[0]).toMatchObject({ kind: "positive", title: "Bruno is excited", sticky: false });
    expect(t[1]).toMatchObject({ kind: "negative", title: "Bruno seems anxious", sticky: true, status: "dashboard_only" });
  });
  it("repeat of the same emotion within 2 min is grouped and moved to top", () => {
    let t = toastFromNotification([], N(1, "anxious", "first"), "Bruno", 0);
    t = toastFromNotification(t, N(2, "excited"), "Bruno", 1000);
    t = toastFromNotification(t, N(3, "anxious", "second"), "Bruno", GROUP_MS - 1);
    expect(t).toHaveLength(2);
    expect(t[0]).toMatchObject({ emotion: "anxious", count: 2, body: "second" });
    t = toastFromNotification(t, N(4, "anxious"), "Bruno", 3 * GROUP_MS);
    expect(t.filter((x) => x.emotion === "anxious")).toHaveLength(2);
  });
  it("expire removes non-sticky after 6 s; dismiss removes by id", () => {
    let t = toastFromNotification([], N(1, "fearful"), "Bruno", 0);
    t = systemToast(t, "AI back online · fused readings resumed", 0);
    expect(expireToasts(t, FADE_MS - 1)).toHaveLength(2);
    const left = expireToasts(t, FADE_MS + 1);
    expect(left.map((x) => x.kind)).toEqual(["negative"]);
    expect(dismissToast(left, left[0].id)).toHaveLength(0);
  });
  it("keeps at most MAX_TOASTS", () => {
    let t: ReturnType<typeof systemToast> = [];
    for (let i = 0; i < 10; i++) t = systemToast(t, `s${i}`, i);
    expect(t).toHaveLength(MAX_TOASTS);
    expect(t[0].title).toBe("s9");
  });
});
