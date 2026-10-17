import { describe, expect, it, vi, beforeEach, afterEach } from "vitest";
import {
  RELOAD_COUNT_KEY,
  buildReloadUrl,
  bumpChunkReload,
  chunkReloadCount,
  clearChunkReload,
  hardReload,
} from "./reload";

function memoryStorage(seed: Record<string, string> = {}) {
  const map = new Map(Object.entries(seed));
  return {
    getItem: (k: string) => (map.has(k) ? map.get(k)! : null),
    setItem: (k: string, v: string) => void map.set(k, v),
    removeItem: (k: string) => void map.delete(k),
  };
}

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

describe("chunk auto-reload loop guard", () => {
  it("starts at zero with no or unparsable history", () => {
    expect(chunkReloadCount(memoryStorage())).toBe(0);
    expect(chunkReloadCount(memoryStorage({ [RELOAD_COUNT_KEY]: "nonsense" }))).toBe(0);
    expect(chunkReloadCount(memoryStorage({ [RELOAD_COUNT_KEY]: "-4" }))).toBe(0);
  });

  it("counts the auto-reloads recorded in the session", () => {
    const storage = memoryStorage();
    const first = bumpChunkReload(storage);
    const second = bumpChunkReload(storage);
    expect(first).toBe(1);
    expect(second).toBe(2);
    expect(chunkReloadCount(storage)).toBe(2);
    expect(storage.getItem(RELOAD_COUNT_KEY)).toBe(String(second));
  });

  it("a successful app start gives a later error the full budget again", () => {
    const storage = memoryStorage({ [RELOAD_COUNT_KEY]: "2" });
    clearChunkReload(storage);
    expect(chunkReloadCount(storage)).toBe(0);
    // Two fresh auto-reloads are available after the clear.
    expect(bumpChunkReload(storage)).toBe(1);
    expect(bumpChunkReload(storage)).toBe(2);
    expect(bumpChunkReload(storage)).toBe(3);
  });

  it("fails soft when storage access throws", () => {
    expect(chunkReloadCount({ getItem: () => { throw new Error("blocked"); } })).toBe(0);
    expect(bumpChunkReload({
      getItem: () => null,
      setItem: () => { throw new Error("blocked"); },
    })).toBe(1);
    expect(() => clearChunkReload({ removeItem: () => { throw new Error("blocked"); } })).not.toThrow();
  });
});
