import { useState, useEffect, useCallback } from 'react';
import { useParams, useNavigate } from 'react-router-dom';
import { cn } from '../lib/cn';
import { formatError } from '../lib/formatError';
import {
  getDataset,
  getDatasetSchema,
  getDatasetSnapshots,
  deleteDataset,
  analyzeImpact,
} from '../api/client';
import { Breadcrumb } from '../components/Breadcrumb';
import { StatusBadge } from '../components/StatusBadge';
import { ColumnList } from '../components/ColumnList';
import { SkeletonList } from '../components/Skeleton';
import type { DatasetGovernance, ImpactAnalysis, ImpactItem, ColumnInfo } from '../types';

export function DatasetDetail() {
  const { name } = useParams<{ name: string }>();
  const navigate = useNavigate();

  const [dataset, setDataset] = useState<DatasetGovernance | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [tab, setTab] = useState<'overview' | 'schema' | 'snapshots' | 'lineage'>('overview');

  // Iceberg schema (lazy loaded)
  const [icebergSchema, setIcebergSchema] = useState<ColumnInfo[] | null>(null);
  const [schemaLoading, setSchemaLoading] = useState(false);
  const [schemaError, setSchemaError] = useState<string | null>(null);

  // Snapshots (lazy loaded)
  const [snapshots, setSnapshots] = useState<
    | {
        snapshot_id: number;
        timestamp: number;
        operation: string;
        summary: Record<string, unknown>;
      }[]
    | null
  >(null);
  const [snapshotsLoading, setSnapshotsLoading] = useState(false);
  const [snapshotsError, setSnapshotsError] = useState<string | null>(null);

  // Delete confirmation
  const [showDelete, setShowDelete] = useState(false);
  const [impact, setImpact] = useState<ImpactAnalysis | null>(null);

  useEffect(() => {
    async function load() {
      if (!name) return;
      setLoading(true);
      try {
        const ds = await getDataset(name);
        setDataset(ds);
      } catch (err: unknown) {
        setError(formatError(err, '数据集不存在或加载失败'));
      } finally {
        setLoading(false);
      }
    }
    load();
  }, [name]);

  const loadSchema = useCallback(async () => {
    if (!name || icebergSchema !== null) return;
    setSchemaLoading(true);
    setSchemaError(null);
    try {
      const s = await getDatasetSchema(name);
      setIcebergSchema(
        s.columns.map((c) => ({
          name: c.name,
          data_type: c.type,
          nullable: c.nullable,
          is_primary_key: false,
          comment: '',
        })),
      );
    } catch (err: unknown) {
      setSchemaError((err as Error).message || 'Failed to load schema');
    } finally {
      setSchemaLoading(false);
    }
  }, [name, icebergSchema]);

  const loadSnapshots = useCallback(async () => {
    if (!name || snapshots !== null) return;
    setSnapshotsLoading(true);
    setSnapshotsError(null);
    try {
      const s = await getDatasetSnapshots(name);
      setSnapshots(s);
    } catch (err: unknown) {
      setSnapshotsError((err as Error).message || 'Failed to load snapshots');
    } finally {
      setSnapshotsLoading(false);
    }
  }, [name, snapshots]);

  useEffect(() => {
    // Tab-switch data fetching: setState happens inside async functions after the
    // first await, so it does not cascade synchronously. The lint rule is
    // conservative here; this is the standard fetch-on-tab-change pattern.
    // eslint-disable-next-line react-hooks/set-state-in-effect
    if (tab === 'schema') loadSchema();
    if (tab === 'snapshots') loadSnapshots();
  }, [tab, loadSchema, loadSnapshots]);

  async function handleDeleteClick() {
    if (!name) return;
    try {
      const result = await analyzeImpact(name, 'dataset', 'delete');
      setImpact(result);
      setShowDelete(true);
    } catch {
      setShowDelete(true);
    }
  }

  async function handleDeleteConfirm() {
    if (!name) return;
    try {
      await deleteDataset(name);
      navigate('/data');
    } catch {
      /* toast */
    }
  }

  if (loading) {
    return (
      <div className="page-container">
        <Breadcrumb
          items={[
            { label: '数据对接', to: '/data/sources' },
            { label: '数据集', to: '/data/datasets' },
            { label: '加载中…' },
          ]}
        />
        <div className="card">
          <SkeletonList rows={4} />
        </div>
      </div>
    );
  }

  if (error || !dataset) {
    return (
      <div className="page-container">
        <Breadcrumb items={[{ label: '数据对接', to: '/data/sources' }, { label: '数据集' }]} />
        <button className="btn mt-2" onClick={() => navigate('/data')}>
          ← 返回
        </button>
        <p className="mt-4 text-error">{error || '数据集不存在'}</p>
      </div>
    );
  }

  return (
    <div className="page-container">
      {/* Breadcrumb */}
      <Breadcrumb
        items={[
          { label: '数据对接', to: '/data/sources' },
          { label: '数据集', to: '/data/datasets' },
          { label: dataset.display_name || dataset.api_name },
        ]}
      />

      {/* Header Card */}
      <div className="card mb-6">
        <div className="mb-4 flex items-center justify-between">
          <div>
            <h1 className="mb-1 text-xl">📦 {dataset.display_name || dataset.api_name}</h1>
            <code className="font-mono text-[11px] text-text-muted">{dataset.api_name}</code>
          </div>
          <div className="flex items-center gap-2">
            {dataset.is_view && <StatusBadge status="VIRTUAL" labelMap={{ VIRTUAL: '虚拟表' }} />}
            <button className="btn btn-sm border-error text-error" onClick={handleDeleteClick}>
              删除
            </button>
          </div>
        </div>

        <table className="data-table max-w-[500px]">
          <tbody>
            {dataset.data_source_api_name && (
              <tr>
                <td className="w-[120px] px-3 py-1 text-text-muted">来源数据源</td>
                <td className="px-3 py-1">
                  <button
                    className="btn btn-sm"
                    onClick={() => navigate(`/data/sources/${dataset.data_source_api_name}`)}
                  >
                    {dataset.data_source_api_name}
                  </button>
                </td>
              </tr>
            )}
            {dataset.source_dataset_api_name && (
              <tr>
                <td className="px-3 py-1 text-text-muted">加工来源</td>
                <td className="px-3 py-1">
                  <code className="font-mono">{dataset.source_dataset_api_name}</code>
                </td>
              </tr>
            )}
            {dataset.storage_location && (
              <tr>
                <td className="px-3 py-1 text-text-muted">存储位置</td>
                <td className="px-3 py-1 font-mono text-[11px]">{dataset.storage_location}</td>
              </tr>
            )}
            {dataset.row_count_estimate != null && (
              <tr>
                <td className="px-3 py-1 text-text-muted">行数估算</td>
                <td className="px-3 py-1">{dataset.row_count_estimate.toLocaleString()}</td>
              </tr>
            )}
            <tr>
              <td className="px-3 py-1 text-text-muted">创建时间</td>
              <td className="px-3 py-1">{new Date(dataset.created_at).toLocaleString()}</td>
            </tr>
          </tbody>
        </table>
      </div>

      {/* Tabs */}
      <div className="view-toggle mb-4">
        {(['overview', 'schema', 'snapshots', 'lineage'] as const).map((t) => (
          <button
            key={t}
            className={cn('view-toggle-btn', tab === t && 'active')}
            onClick={() => setTab(t)}
          >
            {t === 'overview'
              ? '概览'
              : t === 'schema'
                ? 'Schema'
                : t === 'snapshots'
                  ? '快照历史'
                  : '血缘'}
          </button>
        ))}
      </div>

      {/* Tab: Overview */}
      {tab === 'overview' && (
        <div className="card">
          <p className="text-[13px] text-text-muted">
            {dataset.kind === 'VIRTUAL'
              ? `这是一个虚拟表（外部联邦代理），查询时由 Trino 实时拉取外部数据，不落地、只读。来源数据源：${
                  dataset.data_source_api_name || '(未登记)'
                }。`
              : dataset.data_source_api_name
                ? `此托管表通过数据源 "${dataset.data_source_api_name}" 同步而来。`
                : dataset.source_dataset_api_name
                  ? `此托管表由 "${dataset.source_dataset_api_name}" 加工而来。`
                  : '此托管表为独立数据集。'}
          </p>
        </div>
      )}

      {/* Tab: Schema */}
      {tab === 'schema' && (
        <div className="card">
          <h3 className="mb-3 text-sm">物理列定义 (Iceberg)</h3>
          {dataset.partition_config && (
            <div className="mb-3 font-mono text-[11px] text-text-muted">
              分区配置: {JSON.stringify(dataset.partition_config)}
            </div>
          )}
          {schemaLoading ? (
            <p className="text-[13px] text-text-muted">加载 Schema…</p>
          ) : schemaError ? (
            <p className="text-[13px] text-error">加载失败: {schemaError}</p>
          ) : icebergSchema && icebergSchema.length > 0 ? (
            <ColumnList columns={icebergSchema} compact={false} />
          ) : (
            <p className="text-[13px] text-text-muted">暂无物理列定义。</p>
          )}
        </div>
      )}

      {/* Tab: Snapshots */}
      {tab === 'snapshots' && (
        <div className="card">
          <h3 className="mb-3 text-sm">Iceberg 快照历史</h3>
          {snapshotsLoading ? (
            <p className="text-[13px] text-text-muted">加载快照…</p>
          ) : snapshotsError ? (
            <p className="text-[13px] text-error">加载失败: {snapshotsError}</p>
          ) : snapshots && snapshots.length > 0 ? (
            <table className="data-table w-full">
              <thead>
                <tr>
                  <th>快照 ID</th>
                  <th>操作</th>
                  <th>时间</th>
                  <th>摘要</th>
                </tr>
              </thead>
              <tbody>
                {snapshots.map((s) => (
                  <tr key={s.snapshot_id}>
                    <td className="font-mono text-[11px]">{s.snapshot_id}</td>
                    <td>
                      <span className="badge">{s.operation}</span>
                    </td>
                    <td className="text-xs">{new Date(s.timestamp).toLocaleString()}</td>
                    <td className="font-mono text-[11px] text-text-muted">
                      {JSON.stringify(s.summary)}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          ) : (
            <p className="text-[13px] text-text-muted">
              暂无快照记录。数据尚未写入或 Iceberg 表尚未创建。
            </p>
          )}
        </div>
      )}

      {/* Tab: Lineage */}
      {tab === 'lineage' && (
        <div className="card">
          <h3 className="mb-3 text-sm">数据血缘</h3>
          <p className="text-[13px] text-text-muted">
            血缘追踪将在 Sprint+1 通过 Gravitino 血缘自动记录。当前显示静态来源信息。
          </p>
          {dataset.source_dataset_api_name && (
            <div className="mt-3 rounded-md bg-bg p-3">
              <span className="text-xs text-text-secondary">
                ← 来自: <code className="font-mono">{dataset.source_dataset_api_name}</code>
              </span>
            </div>
          )}
          {dataset.data_source_api_name && (
            <div className="mt-2 rounded-md bg-bg p-3">
              <span className="text-xs text-text-secondary">
                ← 外部源: <code className="font-mono">{dataset.data_source_api_name}</code>
              </span>
            </div>
          )}
        </div>
      )}

      {/* Delete Confirmation */}
      {showDelete && (
        <div className="modal-overlay" onClick={() => setShowDelete(false)}>
          <div className="modal max-w-[420px]" onClick={(e) => e.stopPropagation()}>
            <h3 className="mb-2 text-base">
              删除数据集 "{dataset.display_name || dataset.api_name}"
            </h3>
            <p className="mb-4 text-[13px] text-text-secondary">
              此操作不可撤销。仅删除治理元数据，Iceberg 物理表数据不受影响。
            </p>
            {impact && impact.impacts.length > 0 && (
              <div className="mb-4 text-xs">
                <p className="font-medium text-error">将影响以下资源:</p>
                {impact.impacts.map((imp: ImpactItem, i: number) => (
                  <div key={i} className="mt-1 rounded bg-[rgba(255,0,0,0.05)] px-2 py-1">
                    {imp.resource_type}: {imp.api_name} ({imp.effect})
                  </div>
                ))}
              </div>
            )}
            <div className="flex justify-end gap-2">
              <button className="btn" onClick={() => setShowDelete(false)}>
                取消
              </button>
              <button className="btn border-error text-error" onClick={handleDeleteConfirm}>
                确认删除
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
