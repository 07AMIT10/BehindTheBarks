export type PairingKind = "station" | "viewer";

/** Options the backend pairing mint accepts (the kind itself is the first argument). */
export type PairingOptions = { mode_ceiling?: string; label?: string };

/**
 * Mint options for a pairing code. Only viewer invite codes carry a mode ceiling: a station
 * always sees everything it streams, so it is minted without ceiling constraints.
 */
export function pairingOptions(kind: PairingKind, ceiling: string): PairingOptions {
  return kind === "viewer" ? { mode_ceiling: ceiling, label: "viewer" } : {};
}
