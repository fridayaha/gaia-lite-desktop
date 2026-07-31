/**
 * PermissionedRoute — route-level Render Gate (design §8.2).
 *
 * Wraps a route element and checks a single permission before rendering.
 * If disallowed, renders the fallback (typically a Forbidden page) instead.
 * This is the route-level counterpart to PermissionGate — use it to gate
 * entire pages (e.g. /authz/markings requires marking:manage).
 *
 * Unlike PermissionGate (which reads batched decisions), this does a single
 * /authz/check call on mount — route gating is one resource + one action,
 * not a batch, so the explainability endpoint is appropriate here.
 *
 * Usage:
 *   <Route path="/settings/markings" element={
 *     <PermissionedRoute resourceType="MARKING" resourceId="*" action="marking:manage"
 *       fallback={<ForbiddenPage />}>
 *       <MarkingsManagementPage />
 *     </PermissionedRoute>
 *   } />
 */
import { useEffect, useState, type ReactNode } from 'react';
import { checkAccess } from '../../api/permission';
import { ApiError } from '../../api/client';

interface PermissionedRouteProps {
  resourceType: string;
  resourceId: string;
  action: string;
  children: ReactNode;
  fallback?: ReactNode;
}

export function PermissionedRoute({
  resourceType,
  resourceId,
  action,
  children,
  fallback = null,
}: PermissionedRouteProps) {
  const [state, setState] = useState<'loading' | 'allowed' | 'denied' | 'error'>('loading');

  useEffect(() => {
    let cancelled = false;
    checkAccess(resourceType, resourceId, action)
      .then((result) => {
        if (!cancelled) setState(result.decision === 'ALLOW' ? 'allowed' : 'denied');
      })
      .catch((e) => {
        if (!cancelled) {
          // 401/403 → denied; other errors → error state (don't silently fail).
          if (e instanceof ApiError && (e.status === 401 || e.status === 403)) {
            setState('denied');
          } else {
            setState('error');
          }
        }
      });
    return () => {
      cancelled = true;
    };
  }, [resourceType, resourceId, action]);

  if (state === 'loading') {
    return <div className="p-6 text-sm text-fg-muted">加载中…</div>;
  }
  if (state === 'allowed') {
    return <>{children}</>;
  }
  if (state === 'error') {
    return (
      <div className="p-6 text-sm text-error">
        权限校验失败，请稍后重试。
      </div>
    );
  }
  return <>{fallback}</>;
}
