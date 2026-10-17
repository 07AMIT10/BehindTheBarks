/**
 * The repo's standard auth material for backend HTTP calls (the useBackend pattern): base URL
 * plus `Authorization: Bearer <token>` header plus `?token=` query. Households mode has no
 * localStorage token, in which case only the SameSite=Lax cookie rides along — and cross-origin
 * callers (diary, telegram, snippet audio, station revoke) need the token form to avoid dead-401.
 */

/** Backend URL with the token appended in the query (`?token=`, or `&token=` if a query exists). */
export function authedUrl(base: string, path: string, token?: string | null): string {
  const sep = path.includes("?") ? "&" : "?";
  return `${base}${path}${token ? `${sep}token=${encodeURIComponent(token)}` : ""}`;
}

/** Headers carrying the Bearer token, merged with any extra headers. */
export function authedHeaders(token?: string | null, extra: Record<string, string> = {}): Record<string, string> {
  const headers: Record<string, string> = { ...extra };
  if (token) headers["Authorization"] = `Bearer ${token}`;
  return headers;
}

function revokeRequest(base: string, path: string, token?: string | null): { url: string; init: RequestInit } {
  return {
    url: authedUrl(base, path, token),
    init: { method: "DELETE", credentials: "include", headers: authedHeaders(token) },
  };
}

/** Revoke a viewer's access (DELETE /viewers/:id) with standard auth material. */
export function revokeViewerRequest(base: string, viewerId: string, token?: string | null) {
  return revokeRequest(base, `/viewers/${viewerId}`, token);
}

/** Revoke/unpair a station phone (DELETE /stations/:id) with the same auth shape as revokeViewer. */
export function revokeStationRequest(base: string, stationId: string, token?: string | null) {
  return revokeRequest(base, `/stations/${stationId}`, token);
}
