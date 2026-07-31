/**
 * 干跑预览面板（ADR Action Mutation Mapping · §3.7）。
 *
 * 用参数定义生成空表单让用户填示例值，调 previewAction（不落库），
 * 展示会产生的 mutations + 校验错误。HCI：尼尔森「防错」——保存前看到
 * 「这会做什么」。
 */
import { useState } from 'react';
import { previewAction } from '../api/client';
import { useAsyncAction } from '../hooks/useAsyncAction';
import { coerceValue, extractParamDefs } from '../lib/actionForm';
import { formatError } from '../lib/formatError';
import { cn } from '../lib/cn';
import { TextInput } from './ui/TextField';
import type { ActionParameterDef, ActionPreviewResult, ActionTypeRecord } from '../types';

export interface ActionPreviewPanelProps {
  ontology: string;
  objectType: string;
  action: ActionTypeRecord;
}

export function ActionPreviewPanel({ ontology, objectType, action }: ActionPreviewPanelProps) {
  const { loading, error, run } = useAsyncAction();
  const [params, setParams] = useState<Record<string, string>>({});
  const [result, setResult] = useState<ActionPreviewResult | null>(null);

  const paramDefs = extractParamDefs(action).filter((d) => !d.hidden);

  async function handlePreview() {
    setResult(null);
    const payload: Record<string, unknown> = {};
    for (const def of paramDefs) {
      payload[def.api_name] = coerceValue(params[def.api_name] ?? '', def.data_type);
    }
    const res = await run(() =>
      previewAction(ontology, objectType, action.api_name, { parameters: payload }),
    );
    if (res) setResult(res);
  }

  return (
    <div className="card p-3">
      <div className="mb-2 flex items-center justify-between">
        <h4 className="text-xs font-semibold uppercase tracking-wide text-text-secondary">
          预览（干跑，不落库）
        </h4>
        <button
          className={cn('btn btn-xs btn-primary', loading && 'is-loading')}
          onClick={handlePreview}
          disabled={loading}
        >
          {loading ? '运行中…' : '运行预览'}
        </button>
      </div>

      {paramDefs.length > 0 && (
        <div className="mb-2 grid grid-cols-2 gap-2">
          {paramDefs.map((def) => (
            <label key={def.api_name} className="flex flex-col gap-1 text-[11px]">
              <span className="text-text-muted">
                {def.display_name || def.api_name} ({def.data_type})
              </span>
              <TextInput
                inputClassName="form-input text-xs"
                value={params[def.api_name] ?? ''}
                onChange={(v) => setParams((p) => ({ ...p, [def.api_name]: v }))}
                placeholder={def.object_type_ref ? `${def.object_type_ref} 主键` : ''}
              />
            </label>
          ))}
        </div>
      )}

      {error && (
        <div className="rounded-sm border border-error-border bg-error-bg px-2 py-1 text-xs text-error-text">
          {formatError(error)}
        </div>
      )}

      {result && (
        <div
          className={cn(
            'rounded-sm border px-2 py-2 text-xs',
            result.valid
              ? 'border-success-border bg-success-bg text-success-text'
              : 'border-warning-border bg-warning-bg text-warning-text',
          )}
        >
          <div className="font-medium">{result.valid ? '✅ 校验通过' : '⚠ 校验未通过'}</div>
          {result.validation_errors.length > 0 && (
            <ul className="mt-1 list-disc pl-4">
              {result.validation_errors.map((e, i) => (
                <li key={i}>{e}</li>
              ))}
            </ul>
          )}
          {result.mutations.length > 0 && (
            <div className="mt-1.5">
              <div className="font-medium">将产生 {result.mutations.length} 个变更：</div>
              <pre className="mt-1 overflow-x-auto rounded bg-[var(--bg)] p-1.5 text-[10px] text-text">
                {JSON.stringify(result.mutations, null, 2)}
              </pre>
            </div>
          )}
        </div>
      )}
    </div>
  );
}

/** 用于编辑态预览：构造一个最小 ActionTypeRecord 供 ActionPreviewPanel 使用。 */
// eslint-disable-next-line react-refresh/only-export-components
export function makePreviewAction(
  apiName: string,
  displayName: string,
  parameters: ActionParameterDef[],
): ActionTypeRecord {
  return {
    id: 'preview',
    ontology_id: '',
    api_name: apiName,
    display_name: displayName,
    description: '',
    affected_object_type_id: null,
    parameters: { parameters },
    rules: {},
    submission_criteria: {},
    status: 'ACTIVE',
    created_at: '',
    updated_at: '',
  };
}
