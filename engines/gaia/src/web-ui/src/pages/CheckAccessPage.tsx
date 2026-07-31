// CheckAccessPage — the explainability debug panel (ADR-016 §8.5).
//
// Standalone entry: input principal (implicit) + resource_type + resource_id +
// action, output the five-layer decision via AccessDecisionPanel (shared with
// in-place denial UIs). The same panel renders inline wherever a denial occurs
// (DataSourceDetail Access tab, ForbiddenPage) so the user never has to
// re-navigate + re-type to understand "why was I denied".

import { useState, useEffect, useCallback } from 'react';
import {
  checkAccess,
  getMe,
  type CheckAccessResult,
  type Principal,
} from '../api/permission';
import { ApiError } from '../api/client';
import { AccessDecisionPanel } from '../components/permission';

const ACTIONS = [
  'object:view',
  'object:write',
  'action_type:execute',
  'ontology:edit',
  'dataset:view',
];

const RESOURCE_TYPES = ['OBJECT_TYPE', 'ACTION_TYPE', 'ONTOLOGY', 'DATASET'];

export function CheckAccessPage() {
  const [me, setMe] = useState<Principal | null>(null);
  const [resourceType, setResourceType] = useState('OBJECT_TYPE');
  const [resourceId, setResourceId] = useState('');
  const [action, setAction] = useState('object:view');
  const [result, setResult] = useState<CheckAccessResult | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    getMe().then(setMe).catch(() => setMe(null));
  }, []);

  const runCheck = useCallback(async () => {
    if (!resourceId.trim()) {
      setError('请输入资源 ID');
      return;
    }
    setLoading(true);
    setError(null);
    setResult(null);
    try {
      const r = await checkAccess(resourceType, resourceId.trim(), action);
      setResult(r);
    } catch (e) {
      setError(e instanceof ApiError ? e.detail ?? e.message : String(e));
    } finally {
      setLoading(false);
    }
  }, [resourceType, resourceId, action]);

  return (
    <main className="p-6 max-w-4xl mx-auto">
      <h1 className="text-2xl font-semibold mb-1">权限调试</h1>
      <p className="text-sm text-fg-muted mb-6">
        输入资源 + 动作，查看五层权限校验状态、权限来源与缺失权限。
      </p>

      {/* Current principal */}
      <section className="mb-6 p-4 rounded-lg border border-border bg-surface">
        <h2 className="text-sm font-medium mb-2">当前用户</h2>
        {me ? (
          <div className="text-sm space-y-1">
            <div>
              <span className="text-fg-muted">ID：</span>
              <code className="text-accent">{me.id}</code>
              {me.is_anonymous && (
                <span className="ml-2 px-2 py-0.5 rounded bg-warning/20 text-warning text-xs">
                  匿名
                </span>
              )}
            </div>
            {me.roles.length > 0 && (
              <div>
                <span className="text-fg-muted">角色：</span>
                {me.roles.map((r) => (
                  <span key={r} className="mr-1 px-1.5 py-0.5 rounded bg-accent/10 text-accent text-xs">
                    {r}
                  </span>
                ))}
              </div>
            )}
            {me.markings.length > 0 && (
              <div>
                <span className="text-fg-muted">标记：</span>
                {me.markings.map((m) => (
                  <span key={m} className="mr-1 px-1.5 py-0.5 rounded bg-info/10 text-info text-xs">
                    {m}
                  </span>
                ))}
              </div>
            )}
          </div>
        ) : (
          <div className="text-sm text-fg-muted">加载中…</div>
        )}
      </section>

      {/* Input form */}
      <section className="mb-6 p-4 rounded-lg border border-border bg-surface">
        <div className="grid grid-cols-1 md:grid-cols-3 gap-3">
          <label className="block">
            <span className="text-xs text-fg-muted">资源类型</span>
            <select
              value={resourceType}
              onChange={(e) => setResourceType(e.target.value)}
              className="mt-1 w-full px-3 py-2 rounded border border-border bg-bg text-sm"
            >
              {RESOURCE_TYPES.map((t) => (
                <option key={t} value={t}>{t}</option>
              ))}
            </select>
          </label>
          <label className="block">
            <span className="text-xs text-fg-muted">资源 ID / api_name</span>
            <input
              type="text"
              value={resourceId}
              onChange={(e) => setResourceId(e.target.value)}
              onKeyDown={(e) => e.key === 'Enter' && runCheck()}
              placeholder="如 Invoice"
              className="mt-1 w-full px-3 py-2 rounded border border-border bg-bg text-sm"
            />
          </label>
          <label className="block">
            <span className="text-xs text-fg-muted">动作</span>
            <select
              value={action}
              onChange={(e) => setAction(e.target.value)}
              className="mt-1 w-full px-3 py-2 rounded border border-border bg-bg text-sm"
            >
              {ACTIONS.map((a) => (
                <option key={a} value={a}>{a}</option>
              ))}
            </select>
          </label>
        </div>
        <button
          onClick={runCheck}
          disabled={loading || !resourceId.trim()}
          className="mt-3 px-4 py-2 rounded bg-accent text-white text-sm font-medium disabled:opacity-50 hover:bg-accent/90"
        >
          {loading ? '检查中…' : '检查权限'}
        </button>
      </section>

      {error && (
        <div className="mb-4 p-3 rounded bg-error/10 text-error text-sm">{error}</div>
      )}

      {/* Result */}
      {result && (
        <section>
          <AccessDecisionPanel result={result} />
        </section>
      )}
    </main>
  );
}
