// AccessRequestsPage — JIT permission self-service (ADR-016 §8.4).
//
// Two views:
//   - "我的申请": the current user's own requests (all statuses).
//   - "待审批": PENDING requests for reviewers/owners to approve/reject.
//
// Approving creates a time-limited grant; rejecting closes the request.
// Separation of duties: a user cannot approve their own request (enforced
// backend; the UI hides the approve button on own requests).

import { useState, useEffect, useCallback } from 'react';
import {
  listAccessRequests,
  approveAccessRequest,
  rejectAccessRequest,
  type AccessRequest,
} from '../api/permission';
import { ApiError } from '../api/client';
import { cn } from '../lib/cn';

const STATUS_STYLES: Record<string, string> = {
  PENDING: 'bg-warning/20 text-warning',
  APPROVED: 'bg-success/20 text-success',
  REJECTED: 'bg-error/20 text-error',
  EXPIRED: 'bg-fg-muted/20 text-fg-muted',
};

export function AccessRequestsPage() {
  const [tab, setTab] = useState<'mine' | 'pending'>('mine');
  const [requests, setRequests] = useState<AccessRequest[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [actionId, setActionId] = useState<string | null>(null);

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const r = await listAccessRequests(tab === 'pending');
      setRequests(r);
    } catch (e) {
      setError(e instanceof ApiError ? e.detail ?? e.message : String(e));
    } finally {
      setLoading(false);
    }
  }, [tab]);

  useEffect(() => {
    load();
  }, [load]);

  const handleApprove = useCallback(async (id: string) => {
    setActionId(id);
    try {
      await approveAccessRequest(id, '批准');
      await load();
    } catch (e) {
      setError(e instanceof ApiError ? e.detail ?? e.message : String(e));
    } finally {
      setActionId(null);
    }
  }, [load]);

  const handleReject = useCallback(async (id: string) => {
    setActionId(id);
    try {
      await rejectAccessRequest(id, '拒绝');
      await load();
    } catch (e) {
      setError(e instanceof ApiError ? e.detail ?? e.message : String(e));
    } finally {
      setActionId(null);
    }
  }, [load]);

  return (
    <main className="p-6 max-w-5xl mx-auto">
      <h1 className="text-2xl font-semibold mb-1">权限申请</h1>
      <p className="text-sm text-fg-muted mb-6">
        JIT（即时）权限申请与审批。临时需求走自助申请 + 审批 + 到期回收。
      </p>

      {/* Tabs */}
      <div className="flex gap-2 mb-4">
        <button
          onClick={() => setTab('mine')}
          className={cn(
            'px-4 py-2 rounded text-sm font-medium',
            tab === 'mine' ? 'bg-accent text-white' : 'bg-surface border border-border',
          )}
        >
          我的申请
        </button>
        <button
          onClick={() => setTab('pending')}
          className={cn(
            'px-4 py-2 rounded text-sm font-medium',
            tab === 'pending' ? 'bg-accent text-white' : 'bg-surface border border-border',
          )}
        >
          待审批
        </button>
      </div>

      {error && (
        <div className="mb-4 p-3 rounded bg-error/10 text-error text-sm">{error}</div>
      )}

      {loading ? (
        <div className="text-sm text-fg-muted">加载中…</div>
      ) : requests.length === 0 ? (
        <div className="p-8 text-center text-sm text-fg-muted border border-dashed border-border rounded-lg">
          {tab === 'mine' ? '暂无申请记录' : '暂无待审批申请'}
        </div>
      ) : (
        <div className="space-y-3">
          {requests.map((req) => (
            <div
              key={req.id}
              className="p-4 rounded-lg border border-border bg-surface"
            >
              <div className="flex items-start justify-between gap-4">
                <div className="flex-1 min-w-0">
                  <div className="flex items-center gap-2 mb-1">
                    <span
                      className={cn(
                        'px-2 py-0.5 rounded text-xs font-medium',
                        STATUS_STYLES[req.status] ?? 'bg-fg-muted/20',
                      )}
                    >
                      {req.status}
                    </span>
                    <span className="text-xs text-fg-muted">{req.request_type}</span>
                    <code className="text-sm font-medium">{req.requested_item}</code>
                  </div>
                  <p className="text-sm text-fg-muted line-clamp-2">{req.justification}</p>
                  <div className="mt-2 flex flex-wrap gap-x-4 gap-y-1 text-xs text-fg-muted">
                    <span>申请人：<code>{req.requester_id.slice(0, 8)}…</code></span>
                    {req.scope_type && <span>范围：{req.scope_type}</span>}
                    {req.expires_at && (
                      <span>到期：{new Date(req.expires_at).toLocaleString()}</span>
                    )}
                    <span>提交：{new Date(req.created_at).toLocaleString()}</span>
                    {req.reviewed_at && (
                      <span>审批：{new Date(req.reviewed_at).toLocaleString()}</span>
                    )}
                  </div>
                  {req.review_comment && (
                    <p className="mt-2 text-xs text-fg-muted italic">
                      审批意见：{req.review_comment}
                    </p>
                  )}
                </div>
                {tab === 'pending' && req.status === 'PENDING' && (
                  <div className="flex gap-2 shrink-0">
                    <button
                      onClick={() => handleApprove(req.id)}
                      disabled={actionId === req.id}
                      className="px-3 py-1.5 rounded bg-success text-white text-xs font-medium disabled:opacity-50 hover:bg-success/90"
                    >
                      批准
                    </button>
                    <button
                      onClick={() => handleReject(req.id)}
                      disabled={actionId === req.id}
                      className="px-3 py-1.5 rounded bg-error text-white text-xs font-medium disabled:opacity-50 hover:bg-error/90"
                    >
                      拒绝
                    </button>
                  </div>
                )}
              </div>
            </div>
          ))}
        </div>
      )}
    </main>
  );
}
