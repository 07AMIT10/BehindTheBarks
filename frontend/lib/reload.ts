/**
 * Pure function to build a cache-busted reload URL.
 */
export function buildReloadUrl(currentUrl: string, timestamp: number = Date.now()): string {
  const url = new URL(currentUrl);
  url.searchParams.set("_reload", timestamp.toString());
  return url.toString();
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
