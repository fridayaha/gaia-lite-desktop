/**
 * LoginPage — email/password sign-in + sign-up (ADR-016/017 Phase 5, §8.3).
 *
 * Calls Better Auth's signIn/signUp (which set the session cookie), then
 * fetches a JWT for Gaia API calls. The parent route guard redirects to the
 * requested page once `useAuth` reports an authenticated user.
 *
 * Dev fallback: when Better Auth is disabled (AUTH_ENABLED=false), this page
 * is never shown — the route guard lets all traffic through.
 */
import { useState, type FormEvent } from 'react';
import { useAuth } from '../hooks/useAuth';
import { ApiError } from '../api/client';

type Mode = 'signin' | 'signup';

export function LoginPage() {
  const { login, signUp } = useAuth();
  const [mode, setMode] = useState<Mode>('signin');
  const [email, setEmail] = useState('');
  const [password, setPassword] = useState('');
  const [name, setName] = useState('');
  const [error, setError] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);

  async function handleSubmit(e: FormEvent) {
    e.preventDefault();
    setError(null);
    setSubmitting(true);
    try {
      if (mode === 'signin') {
        await login(email.trim(), password);
      } else {
        await signUp(email.trim(), password, name.trim() || email.trim());
      }
      // On success the route guard (watching useAuth) redirects — nothing to
      // do here. If it doesn't, the user can resubmit.
    } catch (err) {
      const msg =
        err instanceof ApiError
          ? err.detail ?? err.message
          : err instanceof Error
            ? err.message
            : '操作失败，请重试';
      setError(msg);
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <div className="min-h-screen flex items-center justify-center bg-bg px-4">
      <main className="w-full max-w-sm">
        <div className="mb-8 text-center">
          <div className="inline-flex items-center gap-2 text-2xl font-semibold text-text">
            <span aria-hidden="true">▲</span>
            <span>Gaia</span>
          </div>
          <p className="mt-2 text-sm text-fg-muted">本体建模平台 · 登录</p>
        </div>

        <form
          onSubmit={handleSubmit}
          className="space-y-4 p-6 rounded-lg border border-border bg-surface"
        >
          {mode === 'signup' && (
            <label className="block">
              <span className="form-label">姓名</span>
              <input
                className="form-input w-full"
                type="text"
                value={name}
                onChange={(e) => setName(e.target.value)}
                placeholder="可选"
                autoComplete="name"
              />
            </label>
          )}
          <label className="block">
            <span className="form-label">邮箱</span>
            <input
              className="form-input w-full"
              type="email"
              value={email}
              onChange={(e) => setEmail(e.target.value)}
              placeholder="you@example.com"
              autoComplete="email"
              required
              autoFocus
            />
          </label>
          <label className="block">
            <span className="form-label">密码</span>
            <input
              className="form-input w-full"
              type="password"
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              placeholder="••••••••"
              autoComplete={mode === 'signin' ? 'current-password' : 'new-password'}
              required
              minLength={8}
            />
          </label>

          {error && (
            <p
              className="text-sm text-error bg-error/10 border border-error/30 rounded px-3 py-2"
              role="alert"
            >
              {error}
            </p>
          )}

          <button
            type="submit"
            className="btn btn-primary w-full"
            disabled={submitting || !email || !password}
          >
            {submitting ? '处理中…' : mode === 'signin' ? '登录' : '注册并登录'}
          </button>

          <div className="text-center text-sm text-fg-muted">
            {mode === 'signin' ? '没有账号？' : '已有账号？'}
            <button
              type="button"
              className="ml-1 text-accent hover:text-accent-hover underline-offset-2 hover:underline"
              onClick={() => {
                setMode(mode === 'signin' ? 'signup' : 'signin');
                setError(null);
              }}
            >
              {mode === 'signin' ? '注册' : '去登录'}
            </button>
          </div>
        </form>

        <p className="mt-6 text-center text-xs text-fg-muted">
          认证由 Better Auth 提供 · Gaia 通过 JWKS 验证 JWT
        </p>
      </main>
    </div>
  );
}
