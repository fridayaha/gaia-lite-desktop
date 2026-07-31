import { useState } from 'react';
import { SchemaTreeBrowser, type SchemaNode } from './SchemaTreeBrowser';
export type { SchemaNode } from './SchemaTreeBrowser';
import { SyncTableDialog } from './SyncTableDialog';
import type { SyncConfig } from './SyncModeSelector';
export type { SyncConfig } from './SyncModeSelector';
import { PreviewTable } from './PreviewTable';
import { ColumnList } from './ColumnList';
import type { TableInfo } from '../types';

interface ExplorerViewProps {
  schemas: SchemaNode[];
  onRefreshSchema?: () => void;
  refreshLoading?: boolean;
  onCreateSync: (tableName: string, config: SyncConfig) => Promise<void>;
  /** Lazily loaded column details keyed by table name. */
  columnMap?: Record<string, TableInfo>;
  /** Fired when user clicks a table to lazy-load its columns. */
  onTableClick?: (tableName: string) => void;
  /** F0: fired when user clicks a table's "登记为虚拟表" button. */
  onRegisterVirtualTable?: (tableName: string) => void;
  sampleData?: { rows: Record<string, unknown>[] } | null;
  sampleLoading?: boolean;
  sampleError?: string | null;
  /** 已有数据集 api_name 列表，透传给同步配置面板的目标选择器（P1）。 */
  existingDatasets?: string[];
}

export function ExplorerView({
  schemas,
  columnMap = {},
  onRefreshSchema,
  refreshLoading,
  onCreateSync,
  onTableClick,
  onRegisterVirtualTable,
  sampleData,
  sampleLoading,
  sampleError,
  existingDatasets,
}: ExplorerViewProps) {
  const [activeTable, setActiveTable] = useState<string | null>(null);
  /** Which table currently has its sync dialog open. */
  const [syncingTable, setSyncingTable] = useState<string | null>(null);
  const [syncSubmitting, setSyncSubmitting] = useState(false);
  /** 右侧详情区 Tab：列信息 / 数据预览（对齐 Snowflake/Databricks 表详情页）。 */
  const [detailTab, setDetailTab] = useState<'columns' | 'data'>('columns');

  function handleTableClick(tableName: string) {
    const prevActive = activeTable;
    setActiveTable((prev) => (prev === tableName ? null : tableName));
    // 切表时重置到「列信息」tab，避免上一张表的预览状态残留
    setDetailTab('columns');
    // If opening (not closing), trigger lazy column load
    if (prevActive !== tableName) {
      onTableClick?.(tableName);
    }
  }

  // Resolve columns for active table (lazy-loaded or inline)
  const activeInfo = activeTable ? columnMap[activeTable] : null;

  async function handleSyncSubmit(config: SyncConfig) {
    if (!syncingTable) return;
    setSyncSubmitting(true);
    try {
      await onCreateSync(syncingTable, config);
      setSyncingTable(null);
    } finally {
      setSyncSubmitting(false);
    }
  }

  return (
    <div className="explore-panel mt-0">
      <div className="flex flex-col">
        {/* Header: title + refresh */}
        {onRefreshSchema && (
          <div className="flex items-center justify-between border-b border-border px-3 py-1.5">
            <span className="text-[11px] font-medium text-text-muted">
              浏览 Schema
            </span>
            <button
              className="btn btn-xs"
              onClick={onRefreshSchema}
              disabled={refreshLoading}
              title="重新从数据源拉取最新的 Schema"
            >
              {refreshLoading ? '刷新中…' : '↻ 刷新'}
            </button>
          </div>
        )}
        {/* Main content: tree + detail side by side */}
        <div className="flex min-h-[400px]">
          {/* Left: Schema tree — pure navigation, no checkboxes */}
          <div className="w-[260px] shrink-0 border-r border-border">
            <SchemaTreeBrowser
              schemas={schemas}
              searchable
              onTableClick={handleTableClick}
              selectedTable={activeTable}
              columnMap={columnMap}
            />
          </div>

          {/* Right: Single-table detail (primary area) or hint */}
          <div className="flex min-w-0 flex-1 flex-col">
            {activeTable ? (
              <>
                {/* Table header with actions */}
                <div className="flex items-center justify-between border-b border-border px-4 py-2.5">
                  <div className="min-w-0">
                    <div className="flex items-baseline gap-2">
                      <h3 className="text-sm font-medium">{activeTable}</h3>
                      {activeInfo && (
                        <span className="text-[11px] text-text-muted">
                          {activeInfo.columns.length} 列
                          {activeInfo.row_count_estimate != null
                            ? ` · ${activeInfo.row_count_estimate.toLocaleString()} 行`
                            : ''}
                        </span>
                      )}
                    </div>
                    {activeInfo?.comment && (
                      <p className="mt-0.5 truncate text-[12px] text-text-secondary" title={activeInfo.comment}>
                        {activeInfo.comment}
                      </p>
                    )}
                  </div>
                  <div className="flex items-center gap-2">
                    {onRegisterVirtualTable && (
                      <button
                        type="button"
                        className="btn btn-sm"
                        title="将此表登记为虚拟表（只读映射，不落地）"
                        onClick={() => onRegisterVirtualTable(activeTable)}
                      >
                        🔗 登记虚拟表
                      </button>
                    )}
                    <button
                      type="button"
                      className="btn btn-sm btn-primary"
                      onClick={() => setSyncingTable(activeTable)}
                    >
                      ↻ 同步此表
                    </button>
                  </div>
                </div>

                {/* Tab Bar：列信息 / 数据预览（对齐 Snowflake/Databricks，不上下堆叠） */}
                <div className="detail-tab-bar" role="tablist">
                  <button
                    type="button"
                    role="tab"
                    aria-selected={detailTab === 'columns'}
                    className={detailTab === 'columns' ? 'detail-tab active' : 'detail-tab'}
                    onClick={() => setDetailTab('columns')}
                  >
                    列信息
                    {activeInfo && (
                      <span className="detail-tab-count">{activeInfo.columns.length}</span>
                    )}
                  </button>
                  <button
                    type="button"
                    role="tab"
                    aria-selected={detailTab === 'data'}
                    className={detailTab === 'data' ? 'detail-tab active' : 'detail-tab'}
                    onClick={() => setDetailTab('data')}
                  >
                    数据预览
                  </button>
                </div>

                {/* Tab 内容区：每个 tab 独立滚动，互不干扰 */}
                <div className="flex-1 overflow-auto p-4">
                  {detailTab === 'columns' ? (
                    activeInfo ? (
                      <ColumnList columns={activeInfo.columns} />
                    ) : (
                      <div className="rounded-md border border-border bg-surface p-3 text-xs text-text-muted">
                        加载列信息中…
                      </div>
                    )
                  ) : (
                    <PreviewTable
                      columns={activeInfo?.columns || []}
                      rows={sampleData?.rows || []}
                      loading={sampleLoading}
                      error={sampleError}
                    />
                  )}
                </div>
              </>
            ) : (
              <div className="flex flex-1 items-center justify-center">
                <div className="text-center text-sm text-text-muted">
                  <div className="mb-1 text-2xl">🔍</div>
                  <p>点击左侧表名查看详情</p>
                  <p className="mt-1 text-xs">选择一张表后，可配置同步或登记为虚拟表</p>
                </div>
              </div>
            )}
          </div>
        </div>
      </div>

      {/* Sync dialog — opens on top of everything, same pattern as RegisterVirtualTable */}
      {syncingTable && activeInfo && (
        <SyncTableDialog
          tableName={syncingTable}
          columns={activeInfo.columns}
          existingDatasets={existingDatasets}
          submitting={syncSubmitting}
          onClose={() => setSyncingTable(null)}
          onSubmit={handleSyncSubmit}
        />
      )}
    </div>
  );
}
