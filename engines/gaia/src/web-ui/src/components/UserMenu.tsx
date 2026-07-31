/**
 * UserMenu — titlebar avatar + dropdown with sign-out (ADR-016/017 §8.3).
 *
 * Shows the signed-in user's initials/email and a dropdown with a sign-out
 * action. Hidden in dev fallback (no Better Auth) — there's no user to show.
 * Mirrors the design language of ThemeToggle (compact rail/titlebar widget).
 */
import { useEffect, useRef, useState } from 'react';
import { useAuth } from '../hooks/useAuth';
import { cn } from '../lib/cn';

function initials(name: string): string {
  const parts = name.split(/[\s@._-]+/).filter(Boolean);
  if (parts.length === 0) return '?';
  if (parts.length === 1) return parts[0].slice(0, 2).toUpperCase();
  return (parts[0][0] + parts[1][0]).toUpperCase();
}

export function UserMenu() {
  const { user, authEnabled, logout } = useAuth();
  const [open, setOpen] = useState(false);
  const [signingOut, setSigningOut] = useState(false);
  const ref = useRef<HTMLDivElement>(null);

  // Close on outside click / Escape.
  useEffect(() => {
    if (!open) return;
    function onDoc(e: MouseEvent) {
      if (ref.current && !ref.current.contains(e.target as Node)) setOpen(false);
    }
    function onKey(e: KeyboardEvent) {
      if (e.key === 'Escape') setOpen(false);
    }
    document.addEventListener('mousedown', onDoc);
    document.addEventListener('keydown', onKey);
    return () => {
      document.removeEventListener('mousedown', onDoc);
      document.removeEventListener('keydown', onKey);
    };
  }, [open]);

  // Dev fallback: no user → render nothing.
  if (!authEnabled || !user) return null;

  async function handleLogout() {
    setSigningOut(true);
    try {
      await logout();
      // The route guard (RequireAuth) observes the cleared session and swaps
      // in LoginPage. No explicit navigate needed.
    } finally {
      setSigningOut(false);
      setOpen(false);
    }
  }

  return (
    <div className="relative self-stretch flex items-center mr-2" ref={ref}>
      <button
        className="flex items-center gap-2 rounded text-text hover:text-text-secondary transition-colors"
        onClick={() => setOpen((v) => !v)}
        aria-label="用户菜单"
        aria-expanded={open}
        aria-haspopup="menu"
        title={user.email}
      >
        <span
          className="w-7 h-7 flex items-center justify-center rounded-full bg-accent/20 text-accent text-xs font-medium shrink-0"
          aria-hidden="true"
        >
          {initials(user.name || user.email)}
        </span>
        <span className="text-sm hidden md:inline">{user.name.split(/[@\s.]/)[0]}</span>
      </button>
      {open && (
        <div
          role="menu"
          className="absolute right-0 top-full mt-1 w-56 rounded-lg border border-border bg-surface shadow-lg z-overlay overflow-hidden"
        >
          <div className="px-3 py-2.5 border-b border-border">
            <div className="text-sm text-text truncate">{user.name}</div>
            <div className="text-xs text-fg-muted truncate">{user.email}</div>
            {user.role && user.role !== 'user' && (
              <span className="mt-1 inline-block px-1.5 py-0.5 rounded bg-accent/10 text-accent text-[10px]">
                {user.role}
              </span>
            )}
          </div>
          <button
            role="menuitem"
            className={cn(
              'w-full text-left px-3 py-2 text-sm text-text hover:bg-white/[0.06] transition-colors',
              signingOut && 'opacity-60 pointer-events-none',
            )}
            onClick={handleLogout}
            disabled={signingOut}
          >
            {signingOut ? '退出中…' : '退出登录'}
          </button>
        </div>
      )}
    </div>
  );
}
