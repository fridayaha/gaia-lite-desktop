import { useEffect, useMemo, useState, useCallback } from 'react';
import { linkDataset, getDatasetSchema } from '../api/client';
import { suggestColumnMappings, type ColumnMappingSuggestion } from '../api/ai';
import {
  autoMatchByColumnName,
  checkTypeCompatibility,
  type TypeCompat,
} from '../lib/columnMapping';
import { formatError } from '../lib/formatError';
import { useToast } from '../hooks/useToast';
import { Modal } from './Modal';
import { Select, SelectOption } from './ui/Select';
import { cn } from '../lib/cn';
import type { DataType, DatasetGovernance, ObjectType } from '../types';

interface DatasetLinkDialogProps {
  open: boolean;
  objectType: ObjectType;
  /** Candidate datasets already filtered by the caller to match storage_type. */
  datasets: DatasetGovernance[];
  ontologyName: string;
  onClose: () => void;
  /** Called after a successful save so the parent can refresh detail. */
  onSaved?: () => void;
}

type DatasetSchemaColumn = { name: string; type: string; nullable: boolean };

/** Per-property mapping row state, combining the column assignment with the
 *  computed type-compatibility verdict (for inline ⚠ markers). */
interface MappingRow {
  property_api_name: string;
  display_name: string;
  data_type: DataType;
  is_primary_key: boolean;
  /** Currently assigned column name ("" = unmapped). */
  column_name: string;
  /** Origin of the current assignment — drives the source badge. */
  source: 'kept' | 'same-name' | 'ai-high' | 'ai-medium' | 'manual' | 'none';
}

/**
 * F4/A1: dataset-link management dialog — handles BOTH first-binding and
 * dataset migration (rebinding an already-linked object to a new dataset).
 *
 * Migration semantics: the object's apiName / displayName / description /
 * primary_key stay unchanged; only the per-property backing_column (and the
 * bound dataset) changes. The dialog surfaces this explicitly when the user
 * picks a different dataset than the currently-bound one.
 *
 * Mapping assistance:
 *  - On dataset switch, deterministic same-name matching pre-fills mappings
 *    (snake_case ↔ camelCase ↔ PascalCase normalized).
 *  - A "✨ AI 智能映射" button asks the LLM to semantically match properties
 *    to columns (handles custName ↔ customer_name, 创建时间 ↔ created_at, …).
 *  - Each row shows the target column's physical type and a type-compat
 *    verdict (exact / compatible / warn / incompatible) so the user can see
 *    whether "数据类型尽量不变" holds. Incompatible types show ⚠ but do NOT
 *    block saving (the user may intentionally remap).
 *
 * Backed by `PATCH /object-types/{type}/dataset-link` (A1). The physical
 * locator (catalog.schema.table) is resolved server-side from the dataset
 * kind, so the client only sends property_api_name + column_name.
 */
export function DatasetLinkDialog({
  open,
  objectType,
  datasets,
  ontologyName,
  onClose,
  onSaved,
}: DatasetLinkDialogProps) {
  const { show: push } = useToast();
  const isVirtual = objectType.storage_type === 'VIRTUAL';

  // Currently bound dataset (derived from first mapped property).
  const initialDatasetApiName =
    objectType.properties.find((p) => p.backing_mapping)?.backing_mapping?.dataset_api_name ?? '';
  const isMigration = !!initialDatasetApiName;

  const buildInitialRows = useCallback((): MappingRow[] => {
    return objectType.properties.map((p) => {
      const col = p.backing_mapping?.backing_column ?? '';
      return {
        property_api_name: p.api_name,
        display_name: p.display_name,
        data_type: p.data_type,
        is_primary_key: p.is_primary_key,
        column_name: col,
        source: col ? 'kept' : 'none',
      };
    });
  }, [objectType.properties]);

  const [selectedDataset, setSelectedDataset] = useState(initialDatasetApiName);
  const [schema, setSchema] = useState<DatasetSchemaColumn[] | null>(null);
  const [schemaLoading, setSchemaLoading] = useState(false);
  const [rows, setRows] = useState<MappingRow[]>(buildInitialRows);
  const [aiLoading, setAiLoading] = useState(false);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);

  // Column name → column (for type lookup). Null when no schema loaded.
  const colByName = useMemo(() => {
    const m = new Map<string, DatasetSchemaColumn>();
    if (schema) for (const c of schema) m.set(c.name, c);
    return m;
  }, [schema]);

  // The dataset currently selected (for the migration banner).
  const selectedDatasetMeta = datasets.find((d) => d.api_name === selectedDataset) ?? null;
  const datasetChanged = isMigration && selectedDataset !== initialDatasetApiName;

  // Load schema whenever the selected dataset changes. On a real switch (not
  // the initial mount), run deterministic same-name matching to pre-fill.
  useEffect(() => {
    if (!open) return;
    if (!selectedDataset) {
      setSchema(null);
      return;
    }
    let cancelled = false;
    setSchemaLoading(true);
    getDatasetSchema(selectedDataset)
      .then((s) => {
        if (cancelled) return;
        const cols = s.columns ?? [];
        setSchema(cols);
        // Only auto-match on a real dataset CHANGE, not the initial mount
        // (initial mount preserves existing mappings via buildInitialRows).
        if (selectedDataset !== initialDatasetApiName) {
          applySameNameMatch(cols);
        }
      })
      .catch((err) => {
        if (cancelled) return;
        setSchema([]);
        push('拉取数据集列失败：' + formatError(err), 'error');
      })
      .finally(() => {
        if (!cancelled) setSchemaLoading(false);
      });
    return () => {
      cancelled = true;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps -- initialDatasetApiName is stable for the dialog's lifetime
  }, [selectedDataset, open]);

  if (!open) return null;

  // ── Mapping helpers ──

  function applySameNameMatch(cols: DatasetSchemaColumn[]) {
    const matched = autoMatchByColumnName(
      objectType.properties.map((p) => ({
        api_name: p.api_name,
        source_column: p.backing_mapping?.backing_column,
      })),
      cols,
    );
    setRows((prev) =>
      prev.map((r) => {
        const col = matched[r.property_api_name] ?? '';
        if (r.column_name === col) return r;
        return { ...r, column_name: col, source: col ? 'same-name' : 'none' };
      }),
    );
  }

  async function handleAiMap() {
    if (!schema || schema.length === 0) {
      setError('请先选择有列信息的数据集');
      return;
    }
    setAiLoading(true);
    setError(null);
    try {
      const suggestions: ColumnMappingSuggestion[] = await suggestColumnMappings(
        objectType.properties.map((p) => ({
          api_name: p.api_name,
          display_name: p.display_name,
          data_type: p.data_type,
        })),
        schema.map((c) => ({ name: c.name, type: c.type })),
      );
      const colNames = new Set(schema.map((c) => c.name));
      // Merge AI suggestions with deterministic same-name fallback:
      // high/medium AI wins; low/empty falls back to same-name match.
      const auto = autoMatchByColumnName(
        objectType.properties.map((p) => ({
          api_name: p.api_name,
          source_column: p.backing_mapping?.backing_column,
        })),
        schema,
      );
      setRows((prev) =>
        prev.map((r) => {
          const ai = suggestions.find((s) => s.property_api_name === r.property_api_name);
          const aiCol = ai?.column_name && colNames.has(ai.column_name) ? ai.column_name : '';
          if (aiCol && (ai!.confidence === 'high' || ai!.confidence === 'medium')) {
            return { ...r, column_name: aiCol, source: ai!.confidence === 'high' ? 'ai-high' : 'ai-medium' };
          }
          const detCol = auto[r.property_api_name];
          if (detCol) return { ...r, column_name: detCol, source: 'same-name' };
          return { ...r, column_name: aiCol, source: aiCol ? 'ai-medium' : 'none' };
        }),
      );
      const mapped = suggestions.filter((s) => s.column_name).length;
      push(`AI 映射完成：${mapped}/${objectType.properties.length} 属性已匹配`, 'success');
    } catch (err) {
      setError('AI 映射失败：' + formatError(err) + '（已保留同名匹配结果）');
    } finally {
      setAiLoading(false);
    }
  }

  function setRowColumn(propApi: string, col: string) {
    setRows((prev) =>
      prev.map((r) =>
        r.property_api_name === propApi ? { ...r, column_name: col, source: col ? 'manual' : 'none' } : r,
      ),
    );
  }

  // ── Save ──

  const mappedEntries = rows.filter((r) => r.column_name.trim() !== '');
  const unmappedRows = rows.filter((r) => !r.column_name.trim());
  const allMapped = unmappedRows.length === 0;

  const handleSave = async () => {
    if (!selectedDataset) {
      setError('请先选择一个数据集');
      return;
    }
    // Strong invariant: every property must be mapped. Unmapped properties
    // would have no data source — the user must delete unwanted properties
    // before binding, not leave them empty.
    if (!allMapped) {
      setError(
        `还有 ${unmappedRows.length} 个属性未映射列，请全部映射后再提交（不需要的属性请先删除）`,
      );
      return;
    }
    setSaving(true);
    setError(null);
    try {
      await linkDataset(
        ontologyName,
        objectType.api_name,
        selectedDataset,
        mappedEntries.map((r) => ({
          property_api_name: r.property_api_name,
          column_name: r.column_name,
        })),
      );
      push(datasetChanged ? '数据集迁移完成' : '数据集关联已保存', 'success');
      onSaved?.();
      onClose();
    } catch (err) {
      setError(formatError(err, '保存关联失败'));
    } finally {
      setSaving(false);
    }
  };

  // ── Type-compat badge per row ──

  function compatForRow(r: MappingRow): { verdict: TypeCompat; colType: string } | null {
    if (!r.column_name) return null;
    const col = colByName.get(r.column_name);
    if (!col) return null;
    return { verdict: checkTypeCompatibility(r.data_type, col.type), colType: col.type };
  }

  const mappedCount = mappedEntries.length;
  const unmappedCount = rows.length - mappedCount;
  const warnCount = rows.filter((r) => {
    const c = compatForRow(r);
    return c && (c.verdict === 'warn' || c.verdict === 'incompatible');
  }).length;

  return (
    <Modal
      open
      onClose={onClose}
      ariaLabel={`管理 ${objectType.display_name} 的数据集关联`}
      overlayClassName=""
      panelClassName="max-w-[680px]"
    >
      <h2 className="mb-1">{datasetChanged ? '迁移数据集' : isMigration ? '管理数据集关联' : '关联数据集'}</h2>
      <p className="mb-4 text-[12px] text-text-muted">
        对象 <code className="font-mono">{objectType.api_name}</code>（{isVirtual ? '虚拟' : '托管'}）
        {datasetChanged && (
          <>
            {' · '}
            <span className="text-accent-text">将 {mappedCount} 个属性迁移到新数据集</span>
          </>
        )}
      </p>

      {/* Migration banner: explain what stays / what changes. */}
      {datasetChanged && (
        <div className="mb-4 rounded-md border border-accent bg-[var(--accent-bg)] px-3 py-2 text-[12px] text-text-secondary">
          <div className="font-semibold text-accent-text">数据集迁移</div>
          <div className="mt-1">
            对象基础信息（apiName / displayName / 主键 / 标题）不变，仅底层 backingColumn 和数据集绑定变更。
          </div>
          <div className="mt-0.5">
            <code className="font-mono text-[11px]">{initialDatasetApiName}</code>
            {' → '}
            <code className="font-mono text-[11px]">{selectedDataset}</code>
          </div>
        </div>
      )}

      {/* Dataset selector */}
      <div className="card mb-4 p-3">
        <div className="mb-2 text-[11px] uppercase tracking-wide text-text-muted">
          {isMigration ? '切换数据集' : '选择数据集'}
        </div>
        {datasets.length === 0 ? (
          <p className="text-[12px] text-text-muted">
            暂无匹配的数据集。请先 {isVirtual ? '在数据源详情登记虚拟表' : '创建同步任务生成托管表'}
            。
          </p>
        ) : (
          <Select
            inputClassName="input"
            value={selectedDataset}
            onChange={setSelectedDataset}
            disabled={saving}
            placeholder="— 未关联 —"
            aria-label="选择数据集"
          >
            <SelectOption value="" label="— 未关联 —" />
            {datasets.map((d) => (
              <SelectOption
                key={d.api_name}
                value={d.api_name}
                label={`${d.api_name}（${d.kind === 'VIRTUAL' ? '虚拟表' : '托管表'}）`}
              />
            ))}
          </Select>
        )}
        {schemaLoading && (
          <div className="mt-2 text-[11px] text-text-muted">拉取数据集列…</div>
        )}
        {schema && !schemaLoading && (
          <div className="mt-2 text-[11px] text-text-muted">
            已加载 {schema.length} 列{selectedDatasetMeta ? ` · ${selectedDatasetMeta.display_name || selectedDatasetMeta.api_name}` : ''}
          </div>
        )}
      </div>

      {/* Column mapping editor */}
      <div className="card mb-4 overflow-hidden p-0">
        <div className="flex items-center justify-between border-b border-border px-3 py-2">
          <div className="text-[12px] font-semibold">
            列映射（{rows.length} 属性 · 已映射 {mappedCount}
            {unmappedCount > 0 && <span className="text-warning"> · 待映射 {unmappedCount}</span>}
            {warnCount > 0 && <span className="text-warning"> · 类型告警 {warnCount}</span>}）
          </div>
          <button
            type="button"
            className="btn btn-xs"
            onClick={handleAiMap}
            disabled={aiLoading || saving || !schema || schema.length === 0}
            title={schema && schema.length > 0 ? '用 AI 按语义匹配属性到列' : '请先选择有列信息的数据集'}
          >
            {aiLoading ? '⏳ AI 映射中…' : '✨ AI 智能映射'}
          </button>
        </div>
        <table className="data-table m-0">
          <thead>
            <tr>
              <th>属性</th>
              <th className="w-[70px]">类型</th>
              <th className="w-[28px] text-center">→</th>
              <th className="w-[180px]">源列</th>
              <th className="w-[90px]">列类型</th>
              <th className="w-[70px]">来源</th>
            </tr>
          </thead>
          <tbody>
            {rows.map((r) => {
              const compat = compatForRow(r);
              const unmapped = !r.column_name;
              return (
                <tr
                  key={r.property_api_name}
                  className={cn(unmapped && 'bg-[color-mix(in_srgb,var(--color-warning)_6%,transparent)]')}
                >
                  <td className="font-mono text-xs">
                    {r.property_api_name}
                    {r.is_primary_key && (
                      <span className="ml-1 rounded-pill bg-[var(--accent-bg-strong)] px-1.5 py-px text-[9px] font-bold text-accent-text">
                        PK
                      </span>
                    )}
                    <div className="text-[10px] font-normal text-text-muted">{r.display_name}</div>
                  </td>
                  <td className="text-[11px] text-text-secondary">{r.data_type}</td>
                  <td className="text-center text-text-muted">→</td>
                  <td>
                    <Select
                      inputClassName="form-select w-full px-1.5 py-[3px] text-[11px]"
                      value={r.column_name}
                      disabled={saving || !selectedDataset || schemaLoading}
                      onChange={(v) => setRowColumn(r.property_api_name, v)}
                      placeholder="—"
                      aria-label={`属性 ${r.property_api_name} 的源列`}
                    >
                      <SelectOption value="" label="—" />
                      {(schema ?? []).map((c) => (
                        <SelectOption key={c.name} value={c.name} label={c.name} />
                      ))}
                    </Select>
                  </td>
                  <td className="text-[11px]">
                    {compat ? (
                      <span
                        className={cn(
                          'font-mono',
                          compat.verdict === 'incompatible' && 'text-danger',
                          compat.verdict === 'warn' && 'text-warning',
                          (compat.verdict === 'exact' || compat.verdict === 'compatible') && 'text-text-muted',
                        )}
                        title={
                          compat.verdict === 'incompatible'
                            ? `类型不兼容：属性 ${r.data_type} ↔ 列 ${compat.colType}`
                            : compat.verdict === 'warn'
                              ? `类型可能有损：属性 ${r.data_type} ↔ 列 ${compat.colType}`
                              : undefined
                        }
                      >
                        {compat.colType}
                        {(compat.verdict === 'incompatible' || compat.verdict === 'warn') && ' ⚠'}
                      </span>
                    ) : (
                      <span className="text-text-muted">—</span>
                    )}
                  </td>
                  <td className="text-[10px]">
                    <SourceBadge source={r.source} />
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>

      {isVirtual && (
        <div className="mb-3 rounded-md border border-border bg-[var(--accent-bg)] px-3 py-2 text-[12px] text-text-secondary">
          虚拟对象的数据集关联为只读定位符，写入仍需通过 Action 走对应托管对象。
        </div>
      )}

      {/* Unmapped-properties block: every property must be mapped before
          submit. Unwanted properties must be deleted in the object editor,
          not left unmapped (silent query data loss). */}
      {unmappedCount > 0 && (
        <div className="mb-3 rounded-md border border-warning bg-[color-mix(in_srgb,var(--color-warning)_12%,transparent)] px-3 py-2 text-[12px] text-text-secondary">
          ⚠ 还有 {unmappedCount} 个属性未映射列，需全部映射后才能提交。不需要的属性请先在对象编辑中删除。
        </div>
      )}

      {error && (
        <div className="mb-3 rounded-md border border-danger bg-[color-mix(in_srgb,var(--color-danger)_10%,transparent)] px-3 py-2 text-[12px] text-danger">
          {error}
        </div>
      )}

      <div className="dialog-actions">
        <button type="button" className="btn" onClick={onClose} disabled={saving}>
          关闭
        </button>
        <button
          type="button"
          className="btn btn-primary"
          onClick={handleSave}
          disabled={saving || datasets.length === 0 || !allMapped}
          title={!allMapped ? '存在未映射的属性，禁止提交' : undefined}
        >
          {saving ? '保存中…' : datasetChanged ? '确认迁移' : '保存关联'}
        </button>
      </div>
    </Modal>
  );
}

/** Tiny badge showing where a row's mapping came from. */
function SourceBadge({ source }: { source: MappingRow['source'] }) {
  const map: Record<MappingRow['source'], { label: string; cls: string }> = {
    kept: { label: '保留', cls: 'text-text-muted' },
    'same-name': { label: '同名', cls: 'text-text-muted' },
    'ai-high': { label: 'AI', cls: 'text-accent-text' },
    'ai-medium': { label: 'AI?', cls: 'text-accent-text' },
    manual: { label: '手动', cls: 'text-text-secondary' },
    none: { label: '', cls: '' },
  };
  const v = map[source];
  if (!v.label) return null;
  return <span className={v.cls}>{v.label}</span>;
}
