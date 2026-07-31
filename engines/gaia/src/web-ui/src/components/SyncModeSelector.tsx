import { TextInput } from './ui/TextField';
import { Select, SelectOption } from './ui/Select';
import type { ColumnInfo } from '../types';

export interface SyncConfig {
  sync_mode: 'full_snapshot' | 'incremental';
  transaction_type: 'snapshot' | 'append';
  incremental_column: string | null;
  target_dataset: string;
}

interface SyncModeSelectorProps {
  tableName: string;
  columns: ColumnInfo[];
  value: SyncConfig;
  onChange: (config: SyncConfig) => void;
  /** 已有数据集 api_name 列表，用于目标数据集选择器（P1 交叉互通）。 */
  existingDatasets?: string[];
}

export function SyncModeSelector({
  tableName,
  columns,
  value,
  onChange,
  existingDatasets,
}: SyncModeSelectorProps) {
  const hasIncrementalColumn = columns.some(
    (c) =>
      c.data_type.toUpperCase().includes('TIMESTAMP') ||
      c.data_type.toUpperCase().includes('DATETIME') ||
      c.data_type.toUpperCase().includes('DATE') ||
      c.name.includes('updated_at') ||
      c.name.includes('created_at') ||
      c.name.includes('modified'),
  );

  const defaultIncCol = columns.find(
    (c) => c.name === 'updated_at' || c.name === 'modified_at' || c.name === 'last_updated',
  );

  const handleModeChange = (mode: 'full_snapshot' | 'incremental') => {
    onChange({
      ...value,
      sync_mode: mode,
      transaction_type: mode === 'incremental' ? 'append' : 'snapshot',
      incremental_column:
        mode === 'incremental' ? value.incremental_column || defaultIncCol?.name || null : null,
    });
  };

  return (
    <div className="flex flex-col gap-1.5 text-[11px]">
      <div className="flex items-center gap-2">
        <span className="min-w-[56px] font-medium text-text-secondary">模式:</span>
        <Select
          inputClassName="form-input w-[120px] px-1.5 py-0.5 text-[11px]"
          value={value.sync_mode}
          onChange={(v) => handleModeChange(v as SyncConfig['sync_mode'])}
          aria-label="同步模式"
        >
          <SelectOption value="full_snapshot" label="全量快照" />
          {hasIncrementalColumn && <SelectOption value="incremental" label="增量同步" />}
        </Select>
        {!hasIncrementalColumn && value.sync_mode === 'incremental' && (
          <span className="text-[10px] text-warning">⚠ 未检测到增量列</span>
        )}
      </div>

      {value.sync_mode === 'incremental' && (
        <div className="flex items-center gap-2">
          <span className="min-w-[56px] font-medium text-text-secondary">增量列:</span>
          <Select
            inputClassName="form-input w-[160px] px-1.5 py-0.5 font-mono text-[11px]"
            value={value.incremental_column || ''}
            onChange={(v) => onChange({ ...value, incremental_column: v || null })}
            placeholder="-- 选择列 --"
            aria-label="增量列"
          >
            <SelectOption value="" label="-- 选择列 --" />
            {columns.map((c) => (
              <SelectOption key={c.name} value={c.name} label={`${c.name} (${c.data_type})`} />
            ))}
          </Select>
        </div>
      )}

      <div className="flex items-center gap-2">
        <span className="min-w-[56px] font-medium text-text-secondary">事务:</span>
        <Select
          inputClassName="form-input w-[120px] px-1.5 py-0.5 text-[11px]"
          value={value.transaction_type}
          onChange={(v) =>
            onChange({
              ...value,
              transaction_type: v as SyncConfig['transaction_type'],
            })
          }
          aria-label="事务类型"
        >
          <SelectOption value="snapshot" label="快照覆盖" />
          <SelectOption value="append" label="追加写入" />
        </Select>
      </div>

      <div className="flex items-center gap-2">
        <span className="min-w-[56px] font-medium text-text-secondary">目标:</span>
        {/* P1: 数据集选择器——自由输入新名（将创建）或从已有数据集中选择（追加）。
            用原生 datalist 兼顾自由输入与下拉提示，比 ComboBox 的 selectedKey 模式更健壮。 */}
        <TextInput
          inputClassName="form-input form-input-mono w-[160px] px-1.5 py-0.5 text-[11px]"
          value={value.target_dataset}
          onChange={(v) => onChange({ ...value, target_dataset: v })}
          placeholder={`${tableName}_raw`}
          aria-label="目标数据集"
          list={`datasets-${tableName}`}
        />
        <datalist id={`datasets-${tableName}`}>
          {(existingDatasets || []).map((name) => (
            <option key={name} value={name} />
          ))}
        </datalist>
        {value.target_dataset && existingDatasets?.includes(value.target_dataset) && (
          <span className="text-[10px] text-text-muted">追加到已有</span>
        )}
        {value.target_dataset && !existingDatasets?.includes(value.target_dataset) && (
          <span className="text-[10px] text-accent-text">将创建</span>
        )}
      </div>
    </div>
  );
}
