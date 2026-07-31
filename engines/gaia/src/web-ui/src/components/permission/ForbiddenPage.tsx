/**
 * ForbiddenPage — shown when a user lacks permission for a route (design §8.2).
 *
 * Rendered by PermissionedRoute when the backend denies access. Follows the
 * "不可见即安全" principle for navigation, but when a user directly navigates
 * to a forbidden URL we tell them why + offer the JIT request path rather
 * than a blank page (research §2.3 — never silently fail).
 */
import { useNavigate } from 'react-router-dom';

interface ForbiddenPageProps {
  /** Optional: the action that was denied (for context). */
  action?: string;
  /** Optional: the resource type that was denied. */
  resourceType?: string;
}

export function ForbiddenPage({ action, resourceType }: ForbiddenPageProps) {
  const navigate = useNavigate();
  return (
    <div className="page-container">
      <div className="card max-w-lg mx-auto mt-12 p-8 text-center">
        <div className="text-4xl mb-3">🔒</div>
        <h2 className="text-lg font-semibold mb-2">无权访问</h2>
        <p className="text-sm text-text-muted mb-1">
          你没有权限访问此页面。
        </p>
        {(action || resourceType) && (
          <p className="text-xs text-text-muted font-mono mb-4">
            {resourceType && `资源: ${resourceType}`}
            {action && ` · 操作: ${action}`}
          </p>
        )}
        <div className="mt-6 flex items-center justify-center gap-3">
          <button className="btn btn-xs" onClick={() => navigate('/')}>
            返回首页
          </button>
          <button
            className="btn btn-xs btn-outline"
            onClick={() => navigate('/authz/requests')}
          >
            申请权限
          </button>
        </div>
      </div>
    </div>
  );
}
