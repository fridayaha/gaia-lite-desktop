import { useState } from 'react';
import { registerVirtualTable } from '../api/client';
import { formatError } from '../lib/formatError';
import { useToast } from '../hooks/useToast';
import { Modal } from './Modal';
import { TextInput } from './ui/TextField';
import type { DatasetGovernance } from '../types';

interface RegisterVirtualTableDialogProps {
  datasourceApiName: string;
  /** Gravitino catalog display name (for the "来源" line). */
  datasourceDisplayName?: string;
  database: string;
  table: string;
  onClose: () => void;
  onRegistered?: (dataset: DatasetGovernance) => void;
}

/**
 * F0: dialog for registering an external table as a kind=VIRTUAL dataset.
 *
 * Defaults the API name to the table name. On success, toasts and invites
 * the user to view the dataset catalog. Errors (409 name conflict, 422
 * unreachable) surface inline via formatError.
 *
 * See docs/design/dataset-ontology-binding.md §4.2.
 */
export function RegisterVirtualTableDialog({
  datasourceApiName,
  datasourceDisplayName,
  database,
  table,
  onClose,
  onRegistered,
}: RegisterVirtualTableDialogProps) {
  const [apiName, setApiName] = useState(table);
  const [displayName, setDisplayName] = useState(table);
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const { show: showToast } = useToast();

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    if (!apiName.trim()) {
      setError('请填写 API name');
      return;
    }
    setSubmitting(true);
    setError(null);
    try {
      const dataset = await registerVirtualTable(datasourceApiName, {
        database,
        table,
        api_name: apiName.trim(),
        display_name: displayName.trim() || table,
      });
      showToast(`已登记虚拟表 ${dataset.api_name}`, 'success');
      onRegistered?.(dataset);
      onClose();
    } catch (err) {
      setError(formatError(err, '登记失败'));
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <Modal open onClose={onClose} ariaLabel="登记虚拟表">
      <form onSubmit={handleSubmit}>
        <h2 className="mb-1">登记虚拟表</h2>
        <p className="mb-4 text-[12px] text-text-muted">
          来源：{' '}
          <code className="font-mono">
            {datasourceDisplayName || datasourceApiName}.{database}.{table}
          </code>
        </p>

        <div className="form-group">
          <label className="form-label" htmlFor="rvt-api-name">
            API name *
          </label>
          <TextInput
            id="rvt-api-name"
            inputClassName="form-input font-mono text-xs"
            value={apiName}
            onChange={setApiName}
            pattern="^[a-z][a-zA-Z0-9_]*$"
            title="小写字母开头，仅含字母、数字、下划线"
            required
            autoFocus
          />
        </div>
        <div className="form-group">
          <label className="form-label" htmlFor="rvt-display-name">
            显示名称
          </label>
          <TextInput
            id="rvt-display-name"
            inputClassName="form-input text-xs"
            value={displayName}
            onChange={setDisplayName}
            placeholder="Orders"
          />
        </div>

        <div className="mb-4 rounded-md bg-[var(--accent-bg)] px-3 py-2 text-[12px] text-text-secondary">
          <span className="mr-1">ℹ</span>
          登记后该表将作为虚拟表进入数据集目录，可被虚拟对象（VIRTUAL）绑定，只读，不落地。
        </div>

        {error && (
          <div className="mb-3 rounded-md border border-error bg-[color-mix(in_srgb,var(--color-error)_10%,transparent)] px-3 py-2 text-[12px] text-error">
            {error}
          </div>
        )}

        <div className="dialog-actions">
          <button type="button" className="btn" onClick={onClose} disabled={submitting}>
            取消
          </button>
          <button type="submit" className="btn btn-primary" disabled={submitting}>
            {submitting ? '登记中…' : '登记'}
          </button>
        </div>
      </form>
    </Modal>
  );
}
