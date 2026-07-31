import { useState } from 'react';
import { SyncModeSelector, type SyncConfig } from './SyncModeSelector';
export type { SyncConfig } from './SyncModeSelector';
import type { TableInfo } from '../types';

interface SyncConfigPanelProps {
  /** Selected tables with their column metadata. */
  selectedTables: TableInfo[];
  /** Called when the user clicks "create all sync tasks". */
  onCreateAllSyncs: (configs: Record<string, SyncConfig>) => void;
  onClearSelection?: () => void;
  loading?: boolean;
  /** 已有数据集 api_name 列表，透传给目标数据集选择器（P1）。 */
  existingDatasets?: string[];
}

const DEFAULT_SYNC_CONFIG: SyncConfig = {
  sync_mode: 'full_snapshot',
  transaction_type: 'snapshot',
  incremental_column: null,
  target_dataset: '',
};

export function SyncConfigPanel({
  selectedTables,
  onCreateAllSyncs,
  onClearSelection,
  loading,
  existingDatasets,
}: SyncConfigPanelProps) {
  const [configs, setConfigs] = useState<Record<string, SyncConfig>>({});

  function getConfig(tableName: string): SyncConfig {
    return (
      configs[tableName] || {
        ...DEFAULT_SYNC_CONFIG,
        target_dataset: `${tableName}_raw`,
      }
    );
  }

  function updateConfig(tableName: string, config: SyncConfig) {
    setConfigs((prev) => ({ ...prev, [tableName]: config }));
  }

  if (selectedTables.length === 0) return null;

  return (
    <div className="flex min-w-[280px] flex-col gap-3 rounded-md border border-border bg-bg p-3">
      <div className="flex items-center justify-between">
        <span className="text-sm font-semibold text-text-secondary">
          已选表 ({selectedTables.length})
        </span>
        {onClearSelection && (
          <button className="btn btn-sm" onClick={onClearSelection}>
            清除选择
          </button>
        )}
      </div>

      {selectedTables.map((table) => {
        const cfg = getConfig(table.name);
        return (
          <div key={table.name} className="rounded-md border border-border bg-surface p-2.5">
            <div className="mb-1.5 font-mono text-sm font-semibold text-text">{table.name}</div>
            <SyncModeSelector
              tableName={table.name}
              columns={table.columns}
              value={cfg}
              onChange={(c) => updateConfig(table.name, c)}
              existingDatasets={existingDatasets}
            />
          </div>
        );
      })}

      <button
        className="btn btn-primary mt-1"
        onClick={() => {
          const merged: Record<string, SyncConfig> = {};
          for (const t of selectedTables) {
            merged[t.name] = getConfig(t.name);
          }
          onCreateAllSyncs(merged);
        }}
        disabled={loading}
      >
        {loading ? '创建中…' : `一键创建同步 (${selectedTables.length})`}
      </button>
    </div>
  );
}
