/**
 * Pure function to build a cache-busted reload URL.
 */
export function buildReloadUrl(currentUrl: string, timestamp: number = Date.now()): string {
  const url = new URL(currentUrl);
  url.searchParams.set("_reload", timestamp.toString());
  return url.toString();
}

/**
 * Auto-reload loop guard for ChunkLoadErrors: remember per-session how many times a hard
 * reload was triggered automatically (sessionStorage survives reloads, dies with the tab)
 * and stop after MAX_AUTO_RELOADS so a persistently broken deploy cannot reload forever.
 */
export const RELOAD_COUNT_KEY = "btb_reload_count";
export const MAX_AUTO_RELOADS = 2;

function readCount(storage: { getItem(k: string): string | null }): number {
  try {
    const raw = storage.getItem(RELOAD_COUNT_KEY);
    const n = raw === null ? Number.NaN : Number.parseInt(raw, 10);
    return Number.isFinite(n) && n > 0 ? n : 0;
  } catch {
    return 0;
  }
}

export function chunkReloadCount(storage: { getItem(k: string): string | null }): number {
  return readCount(storage);
}

/** Records one more auto-reload and returns the running count (1..∞). */
export function bumpChunkReload(storage: { getItem(k: string): string | null; setItem(k: string, v: string): void }): number {
  const next = readCount(storage) + 1;
  try {
    storage.setItem(RELOAD_COUNT_KEY, String(next));
  } catch {
    /* private mode: the count then can only repeat, harmless */
  }
  return next;
}

/** A fresh load reached a working state: give a later ChunkLoadError the full budget again. */
export function clearChunkReload(storage: { removeItem(k: string): void }): void {
  try {
    storage.removeItem(RELOAD_COUNT_KEY);
  } catch {
    /* ignore */
  }
}

/**
 * Force a true hard refresh in the user's browser, clearing caches and bypassing stale assets.
 * Works seamlessly on mobile devices, PWAs, iOS Safari, Android Chrome, and desktop.
 */
export async function hardReload(targetPath?: string): Promise<void> {
  if (typeof window === "undefined") return;

  try {
    // 1. Unregister all service workers if any are active
    if (window.navigator && "serviceWorker" in window.navigator) {
      const registrations = await window.navigator.serviceWorker.getRegistrations();
      await Promise.all(registrations.map((reg) => reg.unregister()));
    }

    // 2. Clear browser CacheStorage entries
    if ("caches" in window && window.caches) {
      const cacheKeys = await window.caches.keys();
      await Promise.all(cacheKeys.map((key) => window.caches.delete(key)));
    }
  } catch (err) {
    console.warn("Cache purge error during hard reload:", err);
  }

  // 3. Navigate with a unique cache-busting timestamp parameter to bypass HTTP/disk cache completely
  const base = window.location?.origin || "https://watch.furryfolks.co";
  const current = targetPath ? new URL(targetPath, base).toString() : (window.location?.href || base);
  const newUrl = buildReloadUrl(current);

  window.location?.replace(newUrl);
}
