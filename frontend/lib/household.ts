// Households-mode auth (T2.3): pure helpers for the Welcome screen and the cookie session.
// The server sets an HttpOnly cookie; every same-origin fetch therefore sends credentials: "include".

export type AuthStatus = {
  enabled: boolean;
  auth_required: boolean;
  mode: "pin" | "households";
};

const MAX_FIELD = 48;

/** True while the Welcome screen (create household / recover) must replace the dashboard. */
export function welcomeVisible(auth: AuthStatus | null, authenticated: boolean): boolean {
  return auth?.mode === "households" && auth.auth_required && !authenticated;
}

/** Field errors for the create/recover forms, keyed the way the form renders them. */
export function validateCreateHousehold(v: { dog_name: string; location: string }): Record<string, string> {
  const errors: Record<string, string> = {};
  if (v.dog_name.trim().length > MAX_FIELD) errors.dog_name = `${MAX_FIELD} characters or fewer`;
  if (v.location.trim().length > MAX_FIELD) errors.location = `${MAX_FIELD} characters or fewer`;
  return errors;
}

export function validateRecoveryKey(v: string): Record<string, string> {
  return v.trim().length === 0 ? { recovery_key: "Paste the recovery key you saved" } : {};
}

/** True when the required "I saved it" checkbox lets the user continue past the shown recovery key. */
export function canLeaveRecoveryStep(saved: boolean): boolean {
  return saved;
}
