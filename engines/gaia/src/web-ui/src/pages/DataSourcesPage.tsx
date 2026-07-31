import { useState, useEffect, useMemo } from 'react';
import { useNavigate } from 'react-router-dom';
import {
  listDataSources,
  listSyncTasks,
  listDatasets,
  deleteDataSource,
  analyzeImpact,
  testConnection,
} from '../api/client';
import { DataSourceForm } from '../components/DataSourceForm';
import { DataSourceCard } from '../components/DataSourceCard';
import { ConfirmDialog } from '../components/ConfirmDialog';
import { CONNECTOR_META } from '../constants/connectorCatalog';
import { ToastView } from '../components/ToastView';
import { SkeletonList } from '../components/Skeleton';
import { EmptyState } from '../components/EmptyState';
import { ListFilterBar } from '../components/ListFilterBar';
import { useToast } from '../hooks/useToast';
import { formatError } from '../lib/formatError';
import type {
  DataSource,
  SyncTask,
  DatasetGovernance,
  ImpactAnalysis,
  ImpactItem,
  ConnectionTestResult,
} from '../types';

/**
 * 数据源列表页（② 数据对接 · 数据源）。
 *
 * 心智定位：管理平台与外部系统的连通。仅关心连接性、同步规则、凭证安全，
 * 不展示落地数据。对应 Palantir Data Connections 应用。
 */
export function DataSourcesPage() {
  const navigate = useNavigate();
  const [datasources, setDatasources] = useState<DataSource[]>([]);
  const [syncTasksMap, setSyncTasksMap] = useState<Record<string, SyncTask[]>>({});
  /** 按数据源 api_name 索引的虚拟表计数（kind=VIRTUAL 且 data_source_api_name 命中）。 */
  const [virtualTableCounts, setVirtualTableCounts] = useState<Record<string, number>>({});
  const [loading, setLoading] = useState(true);
  const [showForm, setShowForm] = useState(false);
  const [editTarget, setEditTarget] = useState<DataSource | null>(null);
  const [error, setError] = useState<string | null>(null);

  // 筛选态
  const [search, setSearch] = useState('');
  const [connectorTypes, setConnectorTypes] = useState<string[]>([]);
  const [statusFilter, setStatusFilter] = useState('');

  // 测试连接 inline 态（按 api_name 索引）
  const [testing, setTesting] = useState<Record<string, boolean>>({});

  // 删除确认
  const [confirmDelete, setConfirmDelete] = useState<{
    targetApiName: string;
    displayName: string;
    onConfirm: () => void;
  } | null>(null);
  const [impactResult, setImpactResult] = useState<ImpactAnalysis | null>(null);
  const { toast, show: showToast, dismiss } = useToast();

  useEffect(() => {
    loadAll();
  }, []);

  // 统一关闭编辑/创建弹窗并刷新
  function handleFormSaved() {
    setShowForm(false);
    setEditTarget(null);
    loadAll();
  }

  async function loadAll() {
    setLoading(true);
    setError(null);
    try {
      const [dss, allDatasets] = await Promise.all([listDataSources(), listDatasets()]);
      setDatasources(dss);
      // 统计每个数据源名下的虚拟表数（VIRTUAL 联邦不落地登记）
      const vtCounts: Record<string, number> = {};
      for (const d of allDatasets as DatasetGovernance[]) {
        if (d.kind === 'VIRTUAL' && d.data_source_api_name) {
          vtCounts[d.data_source_api_name] = (vtCounts[d.data_source_api_name] || 0) + 1;
        }
      }
      setVirtualTableCounts(vtCounts);
      const tasksMap: Record<string, SyncTask[]> = {};
      await Promise.all(
        dss.map(async (ds) => {
          try {
            tasksMap[ds.api_name] = await listSyncTasks(ds.api_name);
          } catch {
            tasksMap[ds.api_name] = [];
          }
        }),
      );
      setSyncTasksMap(tasksMap);
    } catch (err: unknown) {
      setError(formatError(err, '加载数据源失败'));
    } finally {
      setLoading(false);
    }
  }

  // 连接器类型 chips（从实际数据推导可选集 + 计数，label 用目录里的展示名）
  const chipOptions = useMemo(() => {
    const counts: Record<string, number> = {};
    for (const ds of datasources) {
      counts[ds.connector_type] = (counts[ds.connector_type] || 0) + 1;
    }
    return Object.entries(counts).map(([value, count]) => ({
      label: CONNECTOR_META[value]?.label || value.toUpperCase(),
      value,
      count,
    }));
  }, [datasources]);

  // 过滤后的数据源
  const filtered = useMemo(() => {
    const q = search.trim().toLowerCase();
    return datasources.filter((ds) => {
      if (connectorTypes.length > 0 && !connectorTypes.includes(ds.connector_type)) return false;
      if (statusFilter && ds.status !== statusFilter) return false;
      if (q) {
        const hay = `${ds.display_name} ${ds.api_name} ${ds.connector_type}`.toLowerCase();
        if (!hay.includes(q)) return false;
      }
      return true;
    });
  }, [datasources, search, connectorTypes, statusFilter]);

  async function handleDeleteDataSource(ds: DataSource) {
    try {
      const impact = await analyzeImpact(ds.api_name, 'datasource', 'delete');
      setImpactResult(impact);
    } catch {
      setImpactResult(null);
    }
    setConfirmDelete({
      targetApiName: ds.api_name,
      displayName: ds.display_name,
      onConfirm: async () => {
        try {
          await deleteDataSource(ds.api_name);
          setDatasources((p) => p.filter((d) => d.api_name !== ds.api_name));
          showToast('数据源已删除', 'success');
        } catch (err) {
          showToast('删除失败: ' + formatError(err), 'error');
        }
        setConfirmDelete(null);
      },
    });
  }

  async function handleTestConnection(ds: DataSource) {
    setTesting((p) => ({ ...p, [ds.api_name]: true }));
    try {
      const result: ConnectionTestResult = await testConnection(ds.api_name);
      showToast(
        result.message,
        result.success ? 'success' : 'error',
      );
    } catch (err) {
      showToast('测试失败: ' + formatError(err), 'error');
    } finally {
      setTesting((p) => ({ ...p, [ds.api_name]: false }));
    }
  }

  if (loading) {
    return (
      <div className="page-container">
        <div className="page-header">
          <h1>数据源管理</h1>
        </div>
        <div className="flex flex-col gap-3">
          {Array.from({ length: 3 }).map((_, i) => (
            <div key={i} className="card">
              <SkeletonList rows={2} />
            </div>
          ))}
        </div>
      </div>
    );
  }

  if (error) {
    return (
      <div className="page-container">
        <div className="page-header">
          <h1>数据源管理</h1>
        </div>
        <div className="py-6 text-center text-error">
          <p>⚠ 加载失败: {error}</p>
          <button className="btn mt-3" onClick={loadAll}>
            重试
          </button>
        </div>
      </div>
    );
  }

  return (
    <div className="page-container">
      <div className="page-header">
        <div>
          <h1>数据源管理</h1>
          <p className="mt-1 text-[13px] text-text-secondary">
            连接外部数据系统，创建同步任务，将数据导入平台
          </p>
        </div>
        <button className="btn btn-primary" onClick={() => setShowForm(true)}>
          + 添加数据源
        </button>
      </div>

      {(showForm || editTarget) && (
        <DataSourceForm
          initialData={editTarget ?? undefined}
          onCreated={(ds) => {
            setDatasources((p) => [...p, ds]);
            setSyncTasksMap((p) => ({ ...p, [ds.api_name]: [] }));
            setVirtualTableCounts((p) => ({ ...p, [ds.api_name]: 0 }));
            setShowForm(false);
            showToast('数据源创建成功', 'success');
          }}
          onUpdated={() => {
            handleFormSaved();
            showToast('数据源已更新', 'success');
          }}
          onCancel={() => {
            setShowForm(false);
            setEditTarget(null);
          }}
        />
      )}

      {datasources.length > 0 && (
        <ListFilterBar
          searchValue={search}
          onSearchChange={setSearch}
          searchPlaceholder="搜索名称 / api_name / 连接器类型"
          selects={[
            {
              label: '状态',
              value: statusFilter,
              onChange: setStatusFilter,
              options: [
                { label: '全部', value: '' },
                { label: '已连接', value: 'CONNECTED' },
                { label: '未连接', value: 'DISCONNECTED' },
                { label: '异常', value: 'ERROR' },
              ],
            },
          ]}
          chipGroups={[
            {
              label: '连接器',
              options: chipOptions,
              selected: connectorTypes,
              onChange: setConnectorTypes,
            },
          ]}
        />
      )}

      {datasources.length === 0 ? (
        <EmptyState
          icon="📡"
          title="暂无数据源"
          description="点击「添加数据源」连接外部数据库、对象存储或消息队列"
          action={{ label: '+ 添加数据源', onClick: () => setShowForm(true) }}
        />
      ) : filtered.length === 0 ? (
        <EmptyState icon="🔍" title="未匹配到数据源" description="尝试调整搜索条件或清除筛选" />
      ) : (
        <div className="flex flex-col gap-3">
          {filtered.map((ds) => {
            const tasks = syncTasksMap[ds.api_name] || [];
            // 同步概览摘要：任务数 + 最近一次同步状态/时间
            const lastTask = tasks
              .filter((t) => t.last_run_at)
              .sort((a, b) =>
                (b.last_run_at || '').localeCompare(a.last_run_at || ''),
              )[0];
            return (
              <DataSourceCard
                key={ds.id}
                ds={ds}
                variant="list"
                testing={!!testing[ds.api_name]}
                onClick={() => navigate(`/data/sources/${ds.api_name}`)}
                onTestConnection={() => handleTestConnection(ds)}
                onEdit={() => setEditTarget(ds)}
                onDelete={() => handleDeleteDataSource(ds)}
                assetsSummary={{
                  syncCount: tasks.length,
                  virtualTableCount: virtualTableCounts[ds.api_name] || 0,
                  lastStatus: lastTask?.status,
                  lastRunLabel: lastTask
                    ? `最近 ${new Date(lastTask.last_run_at!).toLocaleString()}`
                    : undefined,
                }}
              />
            );
          })}
        </div>
      )}

      {confirmDelete && (
        <ConfirmDialog
          severity={(impactResult?.severity as 'LOW' | 'MEDIUM' | 'HIGH') || 'MEDIUM'}
          title={`删除数据源 "${confirmDelete.displayName}"`}
          message="此操作不可撤销。"
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
