/**
 * RequireAuth — route guard that gates the app behind authentication.
 *
 * Resolves the session via `useAuth`; shows a loading spinner while pending,
 * renders the login page when no session, and the protected children once
 * authenticated. In dev fallback (AUTH_ENABLED=false) it always renders the
 * children — no auth required.
 *
 * (ADR-016/017 Phase 5 §8.3. Design note: the guard is the single source of
 * truth for "is the user logged in". The API client's 401 handler clears the
 * JWT, which makes subsequent /auth/me calls return anonymous, which the
 * guard observes via useSession → redirects to login.)
 */
import type { ReactNode } from 'react';
import { useEffect, useRef, useState } from 'react';
import { useAuth } from '../hooks/useAuth';
import { LoginPage } from '../pages/LoginPage';

export function RequireAuth({ children }: { children: ReactNode }) {
  const { user, loading, jwt, authEnabled } = useAuth();

  // 首次加载标记：只在第一次解析 session 时显示「正在验证会话…」白屏。
  // Better Auth 的 focus-manager 在切 tab 回来时会 refetch session（isPending=true），
  // 此时不应切白屏——否则 LoginPage 卸载、输入框 state 丢失（用户切 tab 回来输入清空）。
  // hasResolved 一旦置 true 就不再回退（即使后续 refetch 期间 isPending 变 true）。
  const hasResolved = useRef(false);
  if (!loading) hasResolved.current = true;
  const showInitialLoading = loading && !hasResolved.current;

  // 超时兜底：首次 session 请求超过 5s，视为无 session（显示登录页），
  // 避免网络/cookie 问题导致永久白屏。
  const [loadTimedOut, setLoadTimedOut] = useState(false);
  useEffect(() => {
    if (!authEnabled || !showInitialLoading) return;
    const t = setTimeout(() => setLoadTimedOut(true), 5000);
    return () => clearTimeout(t);
  }, [authEnabled, showInitialLoading]);

  // Dev fallback: no Better Auth → render the app directly.
  if (!authEnabled) return <>{children}</>;

  // 首次加载（且未超时）才显示 loading 白屏。refetch 期间保持当前 UI。
  if (showInitialLoading && !loadTimedOut) {
    return (
      <div className="min-h-screen flex items-center justify-center bg-bg text-fg-muted text-sm">
        正在验证会话…
      </div>
    );
  }

  // Not signed in → show the login page (no redirect, so a deep link survives
  // sign-in: once authenticated the guard swaps in the children at the same URL).
  if (!user) return <LoginPage />;

  // Wait for the JWT before rendering children: child pages call Gaia APIs
  // (e.g. /auth/me) on mount, and those calls need the JWT attached. Without
  // this gate the first request races the JWT fetch and arrives anonymous.
  if (!jwt) {
    return (
      <div className="min-h-screen flex items-center justify-center bg-bg text-fg-muted text-sm">
        正在获取访问令牌…
      </div>
    );
  }

  return <>{children}</>;
}
