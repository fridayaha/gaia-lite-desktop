// AuditLogsPage — append-only audit log viewer (ADR-016 §8.4).
//
// Queries the /audit-logs endpoint with optional filters (principal_id,
// resource_type, result, layer). Displays decisions in a table with the
// layer that decided + reason. Used by AUDIT_ADMIN / Project Owners for
// compliance traceability + configuration debugging ("which layer denies
// most → fix the config").

import { useState, useEffect, useCallback } from 'react';
import { listAuditLogs, type AuditLog } from '../api/permission';
import { ApiError } from '../api/client';
import { cn } from '../lib/cn';

const LAYER_LABELS: Record<string, string> = {
  IDENTITY: '身份',
  ORG: '组织',
  SPACE: '空间',
  PROJECT: '项目',
  MARKING: '标记',
  ROW: '行级',
  ALLOW: '放行',
};

export function AuditLogsPage() {
  const [logs, setLogs] = useState<AuditLog[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [filters, setFilters] = useState({
    principal_id: '',
    resource_type: '',
    result: '',
    layer: '',
  });
  const [offset, setOffset] = useState(0);
  const PAGE_SIZE = 50;

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const params: Record<string, string | number> = { limit: PAGE_SIZE, offset };
      if (filters.principal_id.trim()) params.principal_id = filters.principal_id.trim();
      if (filters.resource_type.trim()) params.resource_type = filters.resource_type.trim();
      if (filters.result) params.result = filters.result;
      if (filters.layer) params.layer = filters.layer;
      const r = await listAuditLogs(params);
      setLogs(r);
    } catch (e) {
      setError(e instanceof ApiError ? e.detail ?? e.message : String(e));
    } finally {
      setLoading(false);
    }
  }, [filters, offset]);

  useEffect(() => {
    load();
  }, [load]);

  // 筛选条件变化时重置到第一页。
  useEffect(() => {
    setOffset(0);
  }, [filters]);

  return (
    <main className="p-6 max-w-6xl mx-auto">
      <h1 className="text-2xl font-semibold mb-1">审计日志</h1>
      <p className="text-sm text-fg-muted mb-6">
        追加写入、不可篡改的权限决策日志。用于合规追溯与配置排查。
      </p>

      {/* Filters */}
      <div className="mb-4 p-3 rounded-lg border border-border bg-surface flex flex-wrap gap-3 items-end">
        <label className="block">
          <span className="text-xs text-fg-muted">用户 ID</span>
          <input
            type="text"
            value={filters.principal_id}
            onChange={(e) => setFilters({ ...filters, principal_id: e.target.value })}
            placeholder="可选"
            className="mt-1 w-40 px-2 py-1.5 rounded border border-border bg-bg text-sm"
          />
        </label>
        <label className="block">
          <span className="text-xs text-fg-muted">资源类型</span>
          <input
            type="text"
            value={filters.resource_type}
            onChange={(e) => setFilters({ ...filters, resource_type: e.target.value })}
            placeholder="OBJECT_TYPE"
            className="mt-1 w-40 px-2 py-1.5 rounded border border-border bg-bg text-sm"
          />
        </label>
        <label className="block">
          <span className="text-xs text-fg-muted">结果</span>
          <select
            value={filters.result}
            onChange={(e) => setFilters({ ...filters, result: e.target.value })}
            className="mt-1 w-32 px-2 py-1.5 rounded border border-border bg-bg text-sm"
          >
            <option value="">全部</option>
            <option value="ALLOW">ALLOW</option>
            <option value="DENY">DENY</option>
          </select>
        </label>
        <label className="block">
          <span className="text-xs text-fg-muted">拦截层</span>
          <select
            value={filters.layer}
            onChange={(e) => setFilters({ ...filters, layer: e.target.value })}
            className="mt-1 w-32 px-2 py-1.5 rounded border border-border bg-bg text-sm"
          >
            <option value="">全部</option>
            <option value="IDENTITY">身份</option>
            <option value="ORG">组织</option>
            <option value="SPACE">空间</option>
            <option value="PROJECT">项目</option>
            <option value="MARKING">标记</option>
          </select>
        </label>
        <button
          onClick={() => { setOffset(0); load(); }}
          className="px-3 py-1.5 rounded bg-accent text-white text-sm"
        >
          查询
        </button>
      </div>

      {error && (
        <div className="mb-4 p-3 rounded bg-error/10 text-error text-sm">{error}</div>
      )}

      {loading ? (
        <div className="text-sm text-fg-muted">加载中…</div>
      ) : logs.length === 0 ? (
        <div className="p-8 text-center text-sm text-fg-muted border border-dashed border-border rounded-lg">
          暂无审计记录
        </div>
      ) : (
        <div className="overflow-x-auto rounded-lg border border-border">
          <table className="w-full text-sm">
            <thead className="bg-surface border-b border-border">
              <tr>
                <th className="text-left px-3 py-2 font-medium">时间</th>
                <th className="text-left px-3 py-2 font-medium">结果</th>
                <th className="text-left px-3 py-2 font-medium">层</th>
                <th className="text-left px-3 py-2 font-medium">用户</th>
                <th className="text-left px-3 py-2 font-medium">资源</th>
                <th className="text-left px-3 py-2 font-medium">动作</th>
                <th className="text-left px-3 py-2 font-medium">原因</th>
              </tr>
            </thead>
            <tbody>
              {logs.map((log) => (
                <tr key={log.id} className="border-b border-border/50 hover:bg-surface/50">
                  <td className="px-3 py-2 text-xs text-fg-muted whitespace-nowrap">
                    {new Date(log.timestamp).toLocaleString()}
                  </td>
                  <td className="px-3 py-2">
                    <span
                      className={cn(
                        'px-1.5 py-0.5 rounded text-xs font-medium',
                        log.result === 'ALLOW'
                          ? 'bg-success/20 text-success'
                          : 'bg-error/20 text-error',
                      )}
                    >
                      {log.result}
                    </span>
                  </td>
                  <td className="px-3 py-2 text-xs">
                    {log.layer ? (LAYER_LABELS[log.layer] ?? log.layer) : '—'}
                  </td>
                  <td className="px-3 py-2 text-xs">
                    <code>{log.principal_id ? log.principal_id.slice(0, 8) + '…' : 'anonymous'}</code>
                  </td>
                  <td className="px-3 py-2 text-xs">
                    <code>{log.resource_type}:{log.resource_id}</code>
                  </td>
                  <td className="px-3 py-2 text-xs">{log.action}</td>
                  <td className="px-3 py-2 text-xs text-fg-muted max-w-xs truncate" title={log.reason}>
                    {log.reason || '—'}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      {/* 分页 */}
      {!loading && logs.length > 0 && (
        <div className="mt-3 flex items-center justify-between text-xs text-fg-muted">
          <span>
            第 {offset + 1}–{offset + logs.length} 条
          </span>
          <div className="flex gap-2">
            <button
              onClick={() => setOffset((o) => Math.max(0, o - PAGE_SIZE))}
              disabled={offset === 0}
              className="btn btn-xs disabled:opacity-40"
            >
              上一页
            </button>
            <button
              onClick={() => setOffset((o) => o + PAGE_SIZE)}
              disabled={logs.length < PAGE_SIZE}
              className="btn btn-xs disabled:opacity-40"
            >
              下一页
            </button>
          </div>
        </div>
      )}
    </main>
  );
}
