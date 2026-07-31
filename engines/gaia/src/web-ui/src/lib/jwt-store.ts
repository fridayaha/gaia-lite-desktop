/**
 * JWT token store — single source of truth for the Gaia access token.
 *
 * This is a framework-agnostic, synchronous store with a localStorage
 * backing. It exists to break the circular dependency between
 * `client.ts` (needs to read the token) and `auth-client.ts` (sets the
 * token after sign-in): both import THIS module instead of each other.
 *
 * Design (aligned with Better Auth Bearer plugin docs §5 — "Using Bearer
 * Tokens Outside the Auth Client"):
 *   - Sign-in → `setJwt(token)` writes localStorage + in-memory cache
 *     (synchronous, so the very next `request()` call already sees it).
 *   - Every API request → `getJwt()` reads the cache (synchronous).
 *   - Page reload → module init reads localStorage into the cache.
 *   - Sign-out / 401 → `clearJwt()`.
 *
 * No React, no effects, no async — this eliminates the race condition where
 * child components' API requests fire before a `useEffect`-registered token
 * provider is ready.
 */

const JWT_STORAGE_KEY = 'gaia.jwt';

let cachedJwt: string | null = null;

// Read from localStorage on module load (page reload scenario).
// Wrapped in try/catch — localStorage may be unavailable (private mode, SSR).
try {
  cachedJwt = localStorage.getItem(JWT_STORAGE_KEY);
} catch {
  cachedJwt = null;
}

/** Synchronously read the current JWT (or null). For request interceptors. */
export function getJwt(): string | null {
  return cachedJwt;
}

/** Synchronously store a JWT (after sign-in / token refresh). */
export function setJwt(token: string): void {
  cachedJwt = token;
  try {
    localStorage.setItem(JWT_STORAGE_KEY, token);
  } catch {
    /* storage unavailable (private mode) — in-memory only */
  }
}

/** Clear the JWT (on sign-out or 401). */
export function clearJwt(): void {
  cachedJwt = null;
  try {
    localStorage.removeItem(JWT_STORAGE_KEY);
  } catch {
    /* ignore */
  }
}

/** Decode a JWT's payload (no verification — for displaying user info). */
export function decodeJwtPayload(token: string): Record<string, unknown> | null {
  try {
    const payload = token.split('.')[1];
    const json = atob(payload.replace(/-/g, '+').replace(/_/g, '/'));
    return JSON.parse(json);
  } catch {
    return null;
  }
}
