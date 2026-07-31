/**
 * useAuth — top-level authentication state for the Gaia UI (ADR-016/017 §8.3).
 *
 * Wraps Better Auth's `useSession` and layers JWT management on top: after a
 * successful sign-in we fetch a JWT (for Gaia API calls) and store it in the
 * shared `jwt-store`. On sign-out we clear it.
 *
 * JWT is stored synchronously via `jwt-store.setJwt()` — every API request
 * reads it from there (via `client.ts`'s `withAuthHeaders`), with no async
 * provider registration or race conditions.
 *
 * Dev fallback: when `AUTH_ENABLED` is false, the hook reports an always-
 * authenticated "dev" session so the UI renders without Better Auth.
 */
import { useCallback, useEffect, useState } from 'react';
import {
  AUTH_ENABLED,
  authClient,
  fetchJwt,
  signIn as baSignIn,
  signOut as baSignOut,
  useSession,
} from '../lib/auth-client';
import { clearJwt, getJwt } from '../lib/jwt-store';

export interface AuthUser {
  id: string;
  email: string;
  name: string;
  role: string;
}

export interface AuthState {
  /** The authenticated user, or null when not signed in. */
  user: AuthUser | null;
  /** True while the session is being resolved on first load. */
  loading: boolean;
  /** Whether Better Auth is enabled (production mode). */
  authEnabled: boolean;
}

function toAuthUser(session: unknown): AuthUser | null {
  if (!session || typeof session !== 'object') return null;
  const s = session as Record<string, unknown>;
  const user = s.user as Record<string, unknown> | undefined;
  if (!user) return null;
  return {
    id: String(user.id ?? ''),
    email: String(user.email ?? ''),
    name: String(user.name ?? user.email ?? ''),
    role: String(user.role ?? 'user'),
  };
}

export function useAuth() {
  const session = useSession();
  const [jwt, setJwtState] = useState<string | null>(() => getJwt());
  // 登录/注册进行中标记——避免 refetch session 时 RequireAuth 切到 loading 白屏
  // 导致 LoginPage 卸载、输入框清空。
  const [authActionInProgress, setAuthActionInProgress] = useState(false);

  const loading = (AUTH_ENABLED ? session.isPending : false) && !authActionInProgress;
  // Dev fallback: report an always-authenticated dev admin so the UI renders
  // without Better Auth. The backend (AUTHZ_DEV_MODE) resolves this same
  // identity from X-User-Id headers injected by the API client.
  const user = AUTH_ENABLED ? toAuthUser(session.data) : {
    id: 'dev-admin',
    email: 'dev@gaia.local',
    name: 'Dev Admin',
    role: 'PLATFORM_ADMIN',
  };

  // After a session becomes available, fetch a JWT (once per session).
  // On session loss, clear the JWT.
  useEffect(() => {
    if (!AUTH_ENABLED) return;
    if (!session.data) {
      if (getJwt()) {
        clearJwt();
        setJwtState(null);
      }
      return;
    }
    // Session available but no JWT yet — fetch it.
    if (session.data && !getJwt()) {
      void fetchJwt().then((token) => setJwtState(token));
    }
  }, [session.data]);

  const login = useCallback(
    async (email: string, password: string): Promise<void> => {
      setAuthActionInProgress(true);
      try {
        const result = await baSignIn.email({ email, password });
        if (result.error) {
          throw new Error(result.error.message || '登录失败');
        }
        // signIn 不自动刷新 useSession 缓存——手动 refetch，触发 guard 重渲染。
        await session.refetch();
        const token = await fetchJwt();
        setJwtState(token);
      } finally {
        setAuthActionInProgress(false);
      }
    },
    [session],
  );

  const signUp = useCallback(
    async (email: string, password: string, name: string): Promise<void> => {
      setAuthActionInProgress(true);
      try {
        const result = await authClient.signUp.email({ email, password, name });
        if (result.error) {
          throw new Error(result.error.message || '注册失败');
        }
        // Better Auth signs the user in immediately after sign-up.
        await session.refetch();
        const token = await fetchJwt();
        setJwtState(token);
      } finally {
        setAuthActionInProgress(false);
      }
    },
    [session],
  );

  const logout = useCallback(async (): Promise<void> => {
    clearJwt();
    setJwtState(null);
    if (AUTH_ENABLED) {
      await baSignOut();
    }
  }, []);

  return { user, loading, authEnabled: AUTH_ENABLED, jwt, login, signUp, logout };
}
