/**
 * AccessDecisionPanel — reusable five-layer decision visualization (design §8.5).
 *
 * Extracted from CheckAccessPage so the "why was I denied" explanation can
 * render IN PLACE wherever a permission denial occurs (DataSourceDetail
 * Access tab, PermissionGate disabled tooltips, ForbiddenPage) — the user
 * never has to navigate to /authz/check and re-type the resource.
 *
 * This is the "natural feel" UX from research §2.3: a denial isn't a dead
 * end, it's an inline explanation + a one-click request path.
 *
 * Props: a CheckAccessResult (from /authz/check) + optional onRequested
 * callback after a JIT request is submitted.
 */
import { useState, useCallback } from 'react';
import {
  createAccessRequest,
  type CheckAccessResult,
} from '../../api/permission';
import { ApiError } from '../../api/client';
import { cn } from '../../lib/cn';

const LAYER_LABELS: Record<string, string> = {
  identity: '身份认证',
  org: 'Organization',
  space: 'Space 准入',
  project: 'Project RBAC',
  marking: 'Marking MAC',
  row: '行/列级',
};

const LAYER_ORDER = ['identity', 'org', 'space', 'project', 'marking', 'row'];

interface AccessDecisionPanelProps {
  result: CheckAccessResult;
  /** Called after a JIT access request is submitted (e.g. to refresh UI). */
  onRequested?: () => void;
}

export function AccessDecisionPanel({ result, onRequested }: AccessDecisionPanelProps) {
  const [requesting, setRequesting] = useState(false);
  const [requestMsg, setRequestMsg] = useState<string | null>(null);

  const handleRequestAccess = useCallback(async () => {
    if (result.decision === 'ALLOW') return;
    setRequesting(true);
    setRequestMsg(null);
    try {
      const missing = result.missing[0] ?? result.action;
      const req = await createAccessRequest({
        request_type: 'ROLE_ASSIGNMENT',
        requested_item: 'VIEWER',
        justification: `就地申请：缺少 ${missing} 权限访问 ${result.resource_type}:${result.resource_id}`,
        scope_type: 'PROJECT',
        scope_id: null,
      });
      setRequestMsg(`申请已提交（ID: ${req.id.slice(0, 8)}…），等待审批`);
      onRequested?.();
    } catch (e) {
      setRequestMsg(`申请失败：${e instanceof ApiError ? e.detail ?? e.message : String(e)}`);
    } finally {
      setRequesting(false);
    }
  }, [result, onRequested]);

  return (
    <div className="space-y-3">
      {/* Decision badge + intercepting layer */}
      <div
        className={cn(
          'p-3 rounded-lg border-2 flex items-center justify-between',
          result.decision === 'ALLOW'
            ? 'border-success/40 bg-success/10'
            : 'border-error/40 bg-error/10',
        )}
      >
        <div>
          <span
            className={cn(
              'inline-block px-2.5 py-0.5 rounded-full text-xs font-bold',
              result.decision === 'ALLOW' ? 'bg-success text-white' : 'bg-error text-white',
            )}
          >
            {result.decision === 'ALLOW' ? '✓ 允许' : '✗ 拒绝'}
          </span>
          {result.layer && (
            <span className="ml-2 text-xs text-fg-muted">
              拦截层：<code>{result.layer}</code>
            </span>
          )}
        </div>
        {result.reason && (
          <span className="text-xs text-fg-muted max-w-xs text-right">{result.reason}</span>
        )}
      </div>

      {/* Layer stepper */}
      <div className="p-3 rounded-lg border border-border bg-surface">
        <h4 className="text-xs font-medium mb-2 text-fg-muted">五层校验状态</h4>
        <div className="flex items-center gap-1 overflow-x-auto">
          {LAYER_ORDER.map((layer, i) => {
            const passed = result.layers[layer] ?? false;
            return (
              <div key={layer} className="flex items-center">
                <div
                  className={cn(
                    'flex flex-col items-center px-2 py-1.5 rounded border-2 min-w-[72px]',
                    passed ? 'border-success/40 bg-success/10' : 'border-error/40 bg-error/10',
                  )}
                >
                  <span className="text-sm">{passed ? '✓' : '✗'}</span>
                  <span className="text-[10px] mt-0.5">{LAYER_LABELS[layer] ?? layer}</span>
                </div>
                {i < LAYER_ORDER.length - 1 && <div className="w-3 h-px bg-border" />}
              </div>
            );
          })}
        </div>
      </div>

      {/* Provenance (when allowed) */}
      {result.provenance.length > 0 && (
        <div className="p-3 rounded-lg border border-border bg-surface">
          <h4 className="text-xs font-medium mb-2 text-fg-muted">权限来源</h4>
          <ul className="text-xs space-y-1">
            {result.provenance.map((p, i) => (
              <li key={i} className="flex items-center gap-2">
                <span className="text-accent">→</span>
                <code className="text-[11px] bg-bg px-1.5 py-0.5 rounded">{p}</code>
              </li>
            ))}
          </ul>
        </div>
      )}

      {/* Missing + request CTA (when denied) */}
      {result.decision === 'DENY' && (
        <div className="p-3 rounded-lg border border-warning/40 bg-warning/10">
          <h4 className="text-xs font-medium mb-2 text-fg-muted">缺失权限</h4>
          {result.missing.length > 0 ? (
            <ul className="text-xs space-y-1 mb-2">
              {result.missing.map((m) => (
                <li key={m} className="flex items-center gap-2">
                  <span className="text-warning">!</span>
                  <code className="text-[11px] bg-bg px-1.5 py-0.5 rounded">{m}</code>
                </li>
              ))}
            </ul>
          ) : (
            <p className="text-xs text-fg-muted mb-2">
              被 <code>{result.layer}</code> 层拦截（无具体缺失项可申请）
            </p>
          )}
          <button
            onClick={handleRequestAccess}
            disabled={requesting}
            className="px-2.5 py-1 rounded bg-accent text-white text-xs font-medium disabled:opacity-50 hover:bg-accent/90"
          >
            {requesting ? '提交中…' : '申请权限'}
          </button>
          {requestMsg && <span className="ml-2 text-xs text-fg-muted">{requestMsg}</span>}
        </div>
      )}
    </div>
  );
}
