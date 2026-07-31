/**
 * Better Auth React client integration (ADR-016/017 Phase 5, design §8.3).
 *
 * The Better Auth client SDK manages the session (cookie-based, auto-refresh)
 * and exposes a reactive `useSession` hook. After sign-in, we fetch a JWT
 * from the `/api/auth/token` endpoint (the `jwt()` plugin) and store it in
 * the shared `jwt-store` (synchronous, localStorage-backed); every Gaia API
 * call reads it from there via `client.ts`'s `withAuthHeaders`.
 *
 * Architecture (aligned with Better Auth Bearer plugin docs):
 *   - `jwt-store.ts` is the single source of truth for the JWT (no module-
 *     level state scattered across client.ts / auth-client.ts / useAuth.ts).
 *   - Sign-in → `fetchJwt()` → `jwt-store.setJwt(token)` (sync write).
 *   - API requests → `jwt-store.getJwt()` (sync read, via client.ts).
 *   - Page reload → `jwt-store` reads localStorage on module init.
 *
 * This eliminates the race condition where child components' API requests
 * fire before a `useEffect`-registered token provider is ready: there is no
 * async provider registration — `getJwt()` is always synchronous.
 *
 * Two modes (mirrors the backend AUTHZ_DEV_MODE):
 *   - Dev fallback: when VITE_AUTH_ENABLED is unset/false, auth is disabled —
 *     the backend resolves principals from X-User-Id headers. This lets the
 *     UI run without Better Auth during local development.
 *   - Production: VITE_AUTH_ENABLED=true → full Better Auth flow.
 *     VITE_BETTER_AUTH_URL is the auth server URL; empty (default) means
 *     same-origin (dev, via the Vite proxy at /api/auth/*). A full URL
 *     (https://auth.example.com) is used directly in production (requires
 *     Better Auth CORS config + SameSite=None;Secure cookies).
 */
import { createAuthClient } from 'better-auth/react';
import { jwtClient } from 'better-auth/client/plugins';
import { clearJwt, setJwt } from './jwt-store';

/** Whether Better Auth is enabled (production mode).
 *  - VITE_AUTH_ENABLED unset/false → dev fallback (no Better Auth, backend
 *    resolves principals from X-User-Id headers).
 *  - VITE_AUTH_ENABLED=true → Better Auth flow. VITE_BETTER_AUTH_URL is the
 *    auth server URL; empty means same-origin (dev, via the Vite proxy at
 *    /api/auth/*). A full URL (https://auth.example.com) is used directly
 *    (cross-origin, requires Better Auth CORS config).
 */
export const AUTH_ENABLED =
  String(import.meta.env.VITE_AUTH_ENABLED ?? '').toLowerCase() === 'true' ||
  String(import.meta.env.VITE_AUTH_ENABLED ?? '') === '1';

export const authClient = createAuthClient({
  // Empty baseURL → same-origin (Vite proxies /api/auth/* in dev). A full URL
  // is used directly in production.
  baseURL: import.meta.env.VITE_BETTER_AUTH_URL || undefined,
  plugins: [
    // Matches the server's jwt() plugin — exposes .token() and .jwks()
    // client methods for fetching the JWT Gaia's FastAPI verifies.
    jwtClient(),
  ],
});

export const { useSession, signIn, signOut } = authClient;

// ── JWT management ────────────────────────────────────────────────
// The JWT is fetched from Better Auth's /token endpoint (requires a session)
// and stored in the shared `jwt-store` (synchronous, localStorage-backed).
// `client.ts` reads it from there on every API request.

/** Fetch a fresh JWT from Better Auth's /token endpoint (requires a session).
 *  Returns null if auth is disabled (dev fallback) or no session exists.
 *  On success, the JWT is stored synchronously via `jwt-store.setJwt()`
 *  so the very next API request sees it. */
export async function fetchJwt(): Promise<string | null> {
  if (!AUTH_ENABLED) return null;
  try {
    const res = await authClient.token();
    if (res.error) {
      clearJwt();
      return null;
    }
    const token = res.data?.token ?? null;
    if (token) {
      setJwt(token);
    } else {
      clearJwt();
    }
    return token;
  } catch {
    clearJwt();
    return null;
  }
}

/** Decode a JWT's payload (no verification — for displaying user info). */
export { decodeJwtPayload } from './jwt-store';
