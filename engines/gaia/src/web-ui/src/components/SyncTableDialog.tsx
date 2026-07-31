import { useState } from 'react';
import { Modal } from './Modal';
import { SyncModeSelector, type SyncConfig } from './SyncModeSelector';
import type { ColumnInfo } from '../types';

interface SyncTableDialogProps {
  tableName: string;
  columns: ColumnInfo[];
  /** 已有数据集 api_name 列表，用于目标选择器提示可追加。 */
  existingDatasets?: string[];
  submitting?: boolean;
  onClose: () => void;
  onSubmit: (config: SyncConfig) => Promise<void>;
}

const DEFAULT_CONFIG: SyncConfig = {
  sync_mode: 'full_snapshot',
  transaction_type: 'snapshot',
  incremental_column: null,
  target_dataset: '',
};

/**
 * Single-table sync dialog, replacing the old multi-select batch panel.
 * Opens as a modal — same pattern as RegisterVirtualTableDialog.
 */
export function SyncTableDialog({
  tableName,
  columns,
  existingDatasets,
  submitting = false,
  onClose,
  onSubmit,
}: SyncTableDialogProps) {
  const [config, setConfig] = useState<SyncConfig>({
    ...DEFAULT_CONFIG,
    target_dataset: `${tableName}_raw`,
  });
  const [error, setError] = useState<string | null>(null);

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    if (!config.target_dataset.trim()) {
      setError('请填写目标数据集名');
      return;
    }
    setError(null);
    try {
      await onSubmit(config);
    } catch (err) {
      setError(String(err));
    }
  }

  // Determine if target_dataset will create a new dataset or append to existing
  const isExistingTarget = existingDatasets?.includes(config.target_dataset) ?? false;

  return (
    <Modal open onClose={onClose} ariaLabel={`同步 ${tableName}`}>
      <form onSubmit={handleSubmit}>
        <h2 className="mb-1">同步表</h2>
        <p className="mb-4 text-[12px] text-text-muted">
          来源表：{' '}
          <code className="font-mono">{tableName}</code>
          {columns.length > 0 && (
            <span className="ml-2 text-text-muted">
              ({columns.length} 列)
            </span>
          )}
        </p>

        <div className="mb-4 rounded-md border border-border bg-bg p-3">
          <SyncModeSelector
            tableName={tableName}
            columns={columns}
            value={config}
            onChange={setConfig}
            existingDatasets={existingDatasets}
          />
        </div>

        <div className="mb-3 rounded-md bg-[var(--accent-bg)] px-3 py-2 text-[12px] text-text-secondary">
          <span className="mr-1">ℹ</span>
          {isExistingTarget
            ? `将追加数据到已有数据集「${config.target_dataset}」`
            : `将在数据集目录中创建「${config.target_dataset}」并向其中同步数据`}
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
            {submitting ? '创建中…' : '创建同步'}
          </button>
        </div>
      </form>
    </Modal>
  );
}
