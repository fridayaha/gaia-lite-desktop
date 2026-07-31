import { useState, useEffect, useMemo, useCallback } from 'react';
import { useNavigate } from 'react-router-dom';
import {
  listDatasetsPaginated,
  getDatasetOntologyMap,
  deleteDataset,
  analyzeImpact,
  refreshDatasetStats,
} from '../api/client';
import { DataTable, type DataTableColumn } from '../components/ui/DataTable';
import { Pagination } from '../components/ui/Pagination';
import { ConfirmDialog } from '../components/ConfirmDialog';
import { ToastView } from '../components/ToastView';
import { SkeletonList } from '../components/Skeleton';
import { EmptyState } from '../components/EmptyState';
import { useAllowedActions } from '../hooks/useAllowedActions';
import { PermissionGate } from '../components/permission';

const DS_DELETE = 'dataset:delete';
import { ListFilterBar } from '../components/ListFilterBar';
import { StatusBadge } from '../components/StatusBadge';
import { useToast } from '../hooks/useToast';
import { formatError } from '../lib/formatError';
import type {
  DatasetGovernance,
  DatasetOntologyRef,
  ImpactAnalysis,
  ImpactItem,
} from '../types';

/**
 * 数据集列表页（② 数据对接 · 数据集）。
 *
 * 心智定位：管理平台内已落地数据的治理元数据（存储 / Schema / 来源 / 血缘）。
 * 对应 Palantir Projects & Files 应用。与数据源页物理隔离，通过来源列
 * 跳转闭环（→ 数据源页 / 上游数据集详情）。
 */
export function DatasetsPage() {
  const navigate = useNavigate();
  const [datasets, setDatasets] = useState<DatasetGovernance[]>([]);
  const [total, setTotal] = useState(0);
  const [page, setPage] = useState(1);
  const [pageSize, setPageSize] = useState(20);
  /** dataset api_name → 引用它的本体列表（反查映射，全量加载）。 */
  const [ontologyMap, setOntologyMap] = useState<Record<string, DatasetOntologyRef[]>>({});
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  // 筛选态（变化时重置到第 1 页）
  const [search, setSearch] = useState('');
  const [typeFilter, setTypeFilter] = useState('');
  const [ontologyFilter, setOntologyFilter] = useState('');

  // 登记弹窗已移除：后端 register_dataset 仅写 PG 元数据不落地数据，
  // 手动登记会产出空壳数据集（无 schema 无数据），违背第一原则。
  // 数据集主要由 Sync 自动创建；手动登记场景待文件上传能力成熟后补回（P2）。

  // 删除确认
  const [confirmDelete, setConfirmDelete] = useState<{
    targetApiName: string;
    displayName: string;
    onConfirm: () => void;
  } | null>(null);
  const [impactResult, setImpactResult] = useState<ImpactAnalysis | null>(null);
  const { toast, show: showToast, dismiss } = useToast();

  // Ship-the-decision：批量获取当前页数据集的权限决策（设计 §8.2）。
  const datasetIds = useMemo(() => datasets.map((d) => d.api_name), [datasets]);
  const { decisions } = useAllowedActions('DATASET', datasetIds);

  // 拉取当前页（带过滤）。筛选项变化会重置到第 1 页。
  const fetchPage = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const res = await listDatasetsPaginated({
        page,
        pageSize,
        search: search.trim(),
        type: typeFilter,
        ontology: ontologyFilter,
      });
      setDatasets(res.items);
      setTotal(res.total);
    } catch (err: unknown) {
      setError(formatError(err, '加载数据集失败'));
    } finally {
      setLoading(false);
    }
  }, [page, pageSize, search, typeFilter, ontologyFilter]);

  // 首次加载 + 分页/筛选变化时重拉
  useEffect(() => {
    fetchPage();
  }, [fetchPage]);

  // ontology-map 全量加载一次（轻量单 SQL，用于 chips 选项 + 归属列显示）
  useEffect(() => {
    getDatasetOntologyMap()
      .then(setOntologyMap)
      .catch(() => setOntologyMap({}));
  }, []);

  // 类型分类（用户心智维度的「这是什么表」）：
  //   transform — 加工表：基于其他数据集派生（有 source_dataset_api_name）
  //   virtual   — 虚拟表：联邦不落地（kind=VIRTUAL）
  //   managed   — 托管表：数据落地 Iceberg（kind=MANAGED 且非派生）
  // 优先级：transform > virtual > managed（加工表是更有信息量的子类）。
  function typeOf(ds: DatasetGovernance): 'transform' | 'virtual' | 'managed' {
    if (ds.source_dataset_api_name) return 'transform';
    if (ds.kind === 'VIRTUAL') return 'virtual';
    return 'managed';
  }

  // 本体 chips 选项：从全量 ontologyMap 推导（每个本体被多少 dataset 引用）。
  // label 用中文名（display_name 优先，fallback api_name），value 用 api_name。
  const ontologyChipOptions = useMemo(() => {
    const counts: Record<string, number> = {};
    const displayNames: Record<string, string> = {};
    for (const refs of Object.values(ontologyMap)) {
      const seen = new Set<string>();
      for (const r of refs) {
        if (seen.has(r.ontology_api_name)) continue;
        seen.add(r.ontology_api_name);
        counts[r.ontology_api_name] = (counts[r.ontology_api_name] || 0) + 1;
        displayNames[r.ontology_api_name] = r.ontology_display_name || r.ontology_api_name;
      }
    }
    return Object.entries(counts)
      .map(([value, count]) => ({ label: displayNames[value], value, count }))
      .sort((a, b) => b.count - a.count);
  }, [ontologyMap]);

  // 过滤
  async function handleRefreshStats(ds: DatasetGovernance) {
    try {
      const updated = await refreshDatasetStats(ds.api_name);
      setDatasets((p) => p.map((d) => (d.api_name === ds.api_name ? updated : d)));
      showToast(`${ds.api_name}: ${updated.row_count_estimate ?? '—'} 行`, 'success');
    } catch (e) {
      showToast('刷新失败: ' + String(e), 'error');
    }
  }

  const [refreshingAll, setRefreshingAll] = useState(false);
  async function handleRefreshAll() {
    // 刷新当前页所有数据集的行数（分页后不再遍历全量）
    setRefreshingAll(true);
    let ok = 0;
    for (const ds of datasets) {
      try {
        const updated = await refreshDatasetStats(ds.api_name);
        setDatasets((p) => p.map((d) => (d.api_name === ds.api_name ? updated : d)));
        ok++;
      } catch {
        /* best-effort: 单个失败不影响整体 */
      }
    }
    setRefreshingAll(false);
    showToast(`已刷新本页 ${ok}/${datasets.length} 个数据集`, ok > 0 ? 'success' : 'error');
  }

  async function handleDeleteDataset(ds: DatasetGovernance) {
    try {
      const impact = await analyzeImpact(ds.api_name, 'dataset', 'delete');
      setImpactResult(impact);
    } catch {
      setImpactResult(null);
    }
    setConfirmDelete({
      targetApiName: ds.api_name,
      displayName: ds.display_name || ds.api_name,
      onConfirm: async () => {
        try {
          await deleteDataset(ds.api_name);
          showToast('数据集已删除', 'success');
          // 若当前页删完且不是第 1 页，回退一页；否则重拉当前页
          if (datasets.length === 1 && page > 1) {
            setPage((p) => p - 1);
          } else {
            await fetchPage();
          }
        } catch (err) {
          showToast('删除失败: ' + formatError(err), 'error');
        }
        setConfirmDelete(null);
      },
    });
  }

  const columns: DataTableColumn[] = [
    { id: 'name', label: '名称' },
    { id: 'kind', label: '类型' },
    { id: 'ontology', label: '归属本体' },
    { id: 'rows', label: <span className="inline-flex items-center gap-1">预估行数<button className="text-text-muted hover:text-text align-middle disabled:opacity-40" title="刷新本页数据集预估行数" disabled={refreshingAll} onClick={handleRefreshAll}>↻</button></span>, cellClassName: 'text-right' },
    { id: 'updated', label: '更新' },
    { id: 'actions', label: '', cellClassName: 'text-right' },
  ];

  return (
    <div className="page-container">
      <div className="page-header">
        <div>
          <h1>数据集管理</h1>
          <p className="mt-1 text-[13px] text-text-secondary">
            平台内已落地数据的治理元数据：存储、Schema、来源、血缘
          </p>
        </div>
      </div>

      <ListFilterBar
        searchValue={search}
        onSearchChange={(v) => {
          setSearch(v);
          setPage(1);
        }}
        searchPlaceholder="搜索名称 / api_name"
        chipGroups={[
          {
            label: '类型',
            options: [
              { label: '托管表', value: 'managed' },
              { label: '虚拟表', value: 'virtual' },
              { label: '加工表', value: 'transform' },
            ],
            selected: typeFilter ? [typeFilter] : [],
            onChange: (vals) => {
              setTypeFilter(vals.length > 0 ? vals[vals.length - 1] : '');
              setPage(1);
            },
          },
          {
            label: '本体',
            options: ontologyChipOptions,
            selected: ontologyFilter ? [ontologyFilter] : [],
            onChange: (vals) => {
              setOntologyFilter(vals.length > 0 ? vals[vals.length - 1] : '');
              setPage(1);
            },
          },
        ]}
      />

      {loading ? (
        <div className="card">
          <SkeletonList rows={6} />
        </div>
      ) : error ? (
        <div className="py-6 text-center text-error">
          <p>⚠ 加载失败: {error}</p>
          <button className="btn mt-3" onClick={fetchPage}>
            重试
          </button>
        </div>
      ) : total === 0 ? (
        <EmptyState
          icon="📦"
          title="未匹配到数据集"
          description="尝试调整搜索条件或清除筛选，或前往数据源页创建同步任务。"
          action={{ label: '前往数据源 →', onClick: () => navigate('/data/sources') }}
        />
      ) : (
        <div className="card overflow-x-auto">
          <DataTable<DatasetGovernance>
            columns={columns}
            rows={datasets}
            rowKey={(ds) => ds.id}
            aria-label="数据集列表"
            renderCell={(ds: DatasetGovernance, colId: string) => {
              switch (colId) {
                case 'name':
                  return (
                    <button
                      className="flex flex-col items-start text-left"
                      onClick={() => navigate(`/data/datasets/${ds.api_name}`)}
                    >
                      <span className="font-mono text-sm">
                        {ds.kind === 'VIRTUAL' ? '🔗' : '📦'} {ds.display_name || ds.api_name}
                      </span>
                      <code className="font-mono text-[10px] text-text-muted">{ds.api_name}</code>
                    </button>
                  );
                case 'kind': {
                  const t = typeOf(ds);
                  return (
                    <StatusBadge
                      status={t}
                      labelMap={{ managed: '托管表', virtual: '虚拟表', transform: '加工表' }}
                    />
                  );
                }
                case 'ontology': {
                  const refs = ontologyMap[ds.api_name] || [];
                  if (refs.length === 0) {
                    return <span className="text-[11px] text-text-muted">—</span>;
                  }
                  // 多个本体只显示第一个 + “+N”，hover 看全部；点击跳转本体工作台
                  const first = refs[0];
                  const extra = refs.length - 1;
                  return (
                    <button
                      className="font-mono text-[11px] text-accent-text underline decoration-dotted"
                      title={refs.map((r) => r.ontology_display_name || r.ontology_api_name).join(', ')}
                      onClick={() => navigate(`/?ontology=${encodeURIComponent(first.ontology_api_name)}`)}
                    >
                      {first.ontology_display_name || first.ontology_api_name}
                      {extra > 0 && <span className="text-text-muted"> +{extra}</span>}
                    </button>
                  );
                }
                case 'rows':
                  return (
                    <span className="inline-flex items-center justify-end gap-1 font-mono text-[11px] text-text-muted">
                      {ds.row_count_estimate != null ? ds.row_count_estimate.toLocaleString() : '—'}
                      <button
                        className="text-text-muted hover:text-text disabled:opacity-40"
                        aria-label={`刷新数据集 ${ds.display_name || ds.api_name} 预估行数`}
                        title="刷新行数（经 Trino 查询）"
                        onClick={() => handleRefreshStats(ds)}
                      >
                        ↻
                      </button>
                    </span>
                  );
                case 'updated':
                  return (
                    <span className="text-[11px] text-text-muted">
                      {new Date(ds.updated_at).toLocaleDateString()}
                    </span>
                  );
                case 'actions':
                  return (
                    <PermissionGate
                      action={DS_DELETE}
                      resourceId={ds.api_name}
                      decisions={decisions}
                      mode="disable"
                    >
                      <button
                        className="btn btn-xs border-transparent px-1.5 text-[10px] text-error"
                        aria-label={`删除数据集 ${ds.display_name || ds.api_name}`}
                        title="删除数据集（高危，需二次确认）"
                        onClick={() => handleDeleteDataset(ds)}
                      >
                        删除
                      </button>
                    </PermissionGate>
                  );
                default:
                  return null;
              }
            }}
          />
          <div className="flex items-center justify-between border-t border-border px-3 py-2">
            <Pagination
              page={page}
              pageSize={pageSize}
              total={total}
              onChange={setPage}
              onPageSizeChange={(s) => {
                setPageSize(s);
                setPage(1);
              }}
              pageSizeOptions={[10, 20, 50]}
            />
          </div>
        </div>
      )}

      {confirmDelete && (
        <ConfirmDialog
          severity={(impactResult?.severity as 'LOW' | 'MEDIUM' | 'HIGH') || 'MEDIUM'}
          title={`删除数据集 "${confirmDelete.displayName}"`}
          message="此操作不可撤销。仅删除治理元数据，Iceberg 物理表数据不受影响。"
          impacts={(impactResult?.impacts || []) as ImpactItem[]}
          requireName={impactResult?.severity === 'HIGH' ? confirmDelete.displayName : undefined}
          onConfirm={confirmDelete.onConfirm}
          onCancel={() => {
            setConfirmDelete(null);
            setImpactResult(null);
          }}
        />
      )}

      <ToastView toast={toast} onDismiss={dismiss} />
    </div>
  );
}
