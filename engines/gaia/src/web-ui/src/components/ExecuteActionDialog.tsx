import { useState } from 'react';
import { Modal } from './Modal';
import { ActionParameterField } from './ActionParameterField';
import { executeAction, ApiError } from '../api/client';
import { cn } from '../lib/cn';
import { formatError } from '../lib/formatError';
import { coerceValue, extractParamDefs, stringifyParams } from '../lib/actionForm';
import type { ActionTypeRecord, ActionExecutionResult } from '../types';

interface ExecuteActionDialogProps {
  open: boolean;
  onClose: () => void;
  ontology: string;
  objectType: string;
  action: ActionTypeRecord;
  /** Pre-fill parameters (e.g. rid from the currently viewed object). */
  initialParameters?: Record<string, unknown>;
  /** P1 (ADR-011): invoked when an action returns status='applied', so the
   * parent can refresh its view (read-your-writes) or close the dialog. */
  onApplied?: (result: ActionExecutionResult) => void;
}

/**
 * Execute an Action: render a parameter form, submit, and show the result
 * (applied / conflict / validation_failed). Closes the loop from "define an
 * action" to "run it and see the effect".
 *
 * HCI: low-risk actions submit directly; the result panel communicates
 * applied/conflict/validation_failed states clearly. Read-your-writes means
 * an "applied" result is immediately visible if the caller refreshes.
 */
export function ExecuteActionDialog({
  open,
  onClose,
  ontology,
  objectType,
  action,
  initialParameters = {},
  onApplied,
}: ExecuteActionDialogProps) {
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [conflict, setConflict] = useState(false);
  const [params, setParams] = useState<Record<string, string>>(() =>
    stringifyParams(initialParameters),
  );
  const [result, setResult] = useState<ActionExecutionResult | null>(null);

  // Action type parameters live under parameters.parameters (per backend schema).
  // Filter out hidden params (P1 ADR-011 layer-3 hidden flag).
  const paramDefs = extractParamDefs(action).filter((d) => !d.hidden);

  function handleParamChange(name: string, value: string) {
    setParams((p) => ({ ...p, [name]: value }));
  }

  async function handleExecute() {
    setResult(null);
    setError(null);
    setConflict(false);
    setLoading(true);
    const payload: Record<string, unknown> = {};
    for (const def of paramDefs) {
      const raw = params[def.api_name];
      payload[def.api_name] = coerceValue(raw, def.data_type);
    }
    // Carry over any initial params not covered by defs (e.g. rid).
    for (const [k, v] of Object.entries(initialParameters)) {
      if (!(k in payload)) payload[k] = v;
    }

    try {
      // Date.now 在事件处理函数内调用，非 render 期；eslint purity 规则误判
      // eslint-disable-next-line react-hooks/purity
      const now = Date.now();
      const res = await executeAction(ontology, objectType, action.api_name, {
        parameters: payload,
        idempotency_key: `${action.api_name}-${now}`,
      });
      setResult(res);
      if (res.status === 'applied') {
        onApplied?.(res);
      }
    } catch (err) {
      // ADR Action Mutation Mapping §4.2: OCC 冲突 (409) 专属反馈。
      if (err instanceof ApiError && err.status === 409) {
        setConflict(true);
        setError('对象已被他人修改（版本冲突），请刷新对象后重试');
      } else {
        setError(formatError(err));
      }
    } finally {
      setLoading(false);
    }
  }

  const statusLabel: Record<ActionExecutionResult['status'], string> = {
    applied: '已生效',
    accepted: '已受理（重复请求）',
    conflict: '版本冲突',
    validation_failed: '校验失败',
  };

  return (
    <Modal open={open} onClose={onClose} ariaLabel={`执行操作 ${action.display_name}`}>
      <div className="flex flex-col gap-3 p-4">
        <div className="flex items-center justify-between">
          <h2 className="text-base font-semibold text-text">执行操作</h2>
          <span className="rounded-pill bg-[var(--accent-bg)] px-2 py-0.5 text-[11px] text-text-muted">
            {action.display_name}
          </span>
        </div>
        <p className="text-xs text-text-secondary">{action.description || '无描述'}</p>

        {paramDefs.length > 0 && (
          <div className="flex flex-col gap-2">
            <h3 className="text-xs font-medium uppercase text-text-muted">参数</h3>
            {paramDefs.map((def) => (
              <ActionParameterField
                key={def.api_name}
                def={def}
                value={params[def.api_name] ?? ''}
                onChange={(v) => handleParamChange(def.api_name, v)}
                ontology={ontology}
              />
            ))}
          </div>
        )}

        {error && (
          <div className="rounded-sm border border-error-border bg-error-bg px-2 py-1.5 text-xs text-error-text">
            <div>{conflict ? error : formatError(error)}</div>
            {conflict && (
              <button
                className="btn btn-xs btn-primary mt-1.5"
                onClick={() => {
                  setConflict(false);
                  setError(null);
                  onApplied?.({
                    status: 'conflict',
                    action_id: '',
                    affected_objects: {},
                    mutations: [],
                    validation_errors: [],
                  });
                }}
              >
                刷新并重试
              </button>
            )}
          </div>
        )}

        {result && (
          <div
            className={cn(
              'rounded-sm border px-2 py-2 text-xs',
              result.status === 'applied'
                ? 'border-success-border bg-success-bg text-success-text'
                : 'border-warning-border bg-warning-bg text-warning-text',
            )}
          >
            <div className="font-medium">状态：{statusLabel[result.status]}</div>
            {result.action_id && (
              <div className="mt-1 text-text-muted">action_id: {result.action_id}</div>
            )}
            {Object.keys(result.affected_objects).length > 0 && (
              <div className="mt-1">
                影响对象：
                {Object.entries(result.affected_objects).map(([id, ver]) => (
                  <span key={id} className="ml-1 font-mono text-[10px]">
                    {id}@v{ver}
                  </span>
                ))}
              </div>
            )}
            {result.validation_errors.length > 0 && (
              <ul className="mt-1 list-disc pl-4">
                {result.validation_errors.map((e, i) => (
                  <li key={i}>{e}</li>
                ))}
              </ul>
            )}
            {result.conflict_details && (
              <pre className="mt-1 overflow-x-auto text-[10px]">
                {JSON.stringify(result.conflict_details, null, 2)}
              </pre>
            )}
            {result.forbidden_objects && result.forbidden_objects.length > 0 && (
              <div className="mt-1 text-text-muted">
                部分对象无写权限被跳过：{result.forbidden_objects.join(', ')}
              </div>
            )}
            {result.status === 'applied' && (
              <div className="mt-1 text-text-muted">
                变更已写入 object_state，立即可查询（read-your-writes）。
              </div>
            )}
          </div>
        )}

        <div className="flex justify-end gap-2 pt-1">
          <button className="btn btn-ghost" onClick={onClose} disabled={loading}>
            关闭
          </button>
          <button
            className={cn('btn btn-primary', loading && 'is-loading')}
            onClick={handleExecute}
            disabled={loading}
          >
            {loading ? '执行中…' : '执行'}
          </button>
        </div>
      </div>
    </Modal>
  );
}
