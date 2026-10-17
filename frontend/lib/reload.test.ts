import { describe, expect, it, vi, beforeEach, afterEach } from "vitest";
import { buildReloadUrl, hardReload } from "./reload";

describe("buildReloadUrl", () => {
  it("appends _reload timestamp parameter to clean URL", () => {
    const url = buildReloadUrl("https://watch.furryfolks.co/dashboard", 1234567890);
    expect(url).toBe("https://watch.furryfolks.co/dashboard?_reload=1234567890");
  });

  it("updates existing _reload parameter without duplicating", () => {
    const url = buildReloadUrl("https://watch.furryfolks.co/?_reload=111&foo=bar", 222);
    expect(url).toContain("_reload=222");
    expect(url).toContain("foo=bar");
    expect(url.match(/_reload=/g)?.length).toBe(1);
  });
});

describe("hardReload", () => {
  const origWindow = (globalThis as unknown as { window?: unknown }).window;

  beforeEach(() => {
    vi.restoreAllMocks();
  });

  afterEach(() => {
    if (origWindow === undefined) {
      delete (globalThis as unknown as { window?: unknown }).window;
    } else {
      (globalThis as unknown as { window?: unknown }).window = origWindow;
    }
  });

  it("returns gracefully if window is undefined", async () => {
    delete (globalThis as unknown as { window?: unknown }).window;
    await expect(hardReload()).resolves.toBeUndefined();
  });

  it("clears caches and redirects with cache-busting url when window is present", async () => {
    const replaceMock = vi.fn();
    const deleteCacheMock = vi.fn().mockResolvedValue(true);
    const unregisterMock = vi.fn().mockResolvedValue(true);

    (globalThis as unknown as { window: unknown }).window = {
      location: {
        href: "https://watch.furryfolks.co/dashboard",
        origin: "https://watch.furryfolks.co",
        replace: replaceMock,
      },
      caches: {
        keys: vi.fn().mockResolvedValue(["v1-cache", "v2-cache"]),
        delete: deleteCacheMock,
      },
      navigator: {
        serviceWorker: {
          getRegistrations: vi.fn().mockResolvedValue([{ unregister: unregisterMock }]),
        },
      },
    };

    await hardReload();

    expect(unregisterMock).toHaveBeenCalledTimes(1);
    expect(deleteCacheMock).toHaveBeenCalledWith("v1-cache");
    expect(deleteCacheMock).toHaveBeenCalledWith("v2-cache");
    expect(replaceMock).toHaveBeenCalledTimes(1);
    const destination = replaceMock.mock.calls[0][0] as string;
    expect(destination).toContain("/dashboard?_reload=");
  });
});
