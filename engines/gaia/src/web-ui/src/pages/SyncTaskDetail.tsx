import { useState, useEffect } from 'react';
import { useParams, useNavigate } from 'react-router-dom';
import {
  getSyncTask,
  startSyncTask,
  stopSyncTask,
  deleteSyncTask,
  refreshSyncTask,
} from '../api/client';
import { cn } from '../lib/cn';
import { formatError } from '../lib/formatError';
import { Breadcrumb } from '../components/Breadcrumb';
import { SyncTaskCard } from '../components/SyncTaskCard';
import { SyncModeSelector } from '../components/SyncModeSelector';
import { SkeletonList } from '../components/Skeleton';
import { ConfirmDialog } from '../components/ConfirmDialog';
import type { SyncTask, ColumnInfo } from '../types';

export function SyncTaskDetail() {
  const { name } = useParams<{ name: string }>();
  const navigate = useNavigate();

  const [task, setTask] = useState<SyncTask | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [tab, setTab] = useState<'overview' | 'config'>('overview');
  const [confirmDelete, setConfirmDelete] = useState(false);

  useEffect(() => {
    async function load() {
      if (!name) return;
      setLoading(true);
      try {
        const t = await getSyncTask(name);
        setTask(t);
        // Reconcile with SeaTunnel so the detail page shows the job's
        // real state (RUNNING / FINISHED / FAILED) instead of the stale
        // DB row. refreshSyncTask persists the truth server-side.
        try {
          const r = await refreshSyncTask(name);
          setTask(r);
        } catch {
          // SeaTunnel unreachable — keep the getSyncTask result.
        }
      } catch (err: unknown) {
        setError(formatError(err, '同步任务不存在或加载失败'));
      } finally {
        setLoading(false);
      }
    }
    load();
  }, [name]);

  async function handleStart() {
    if (!name || !task) return;
    const t = await startSyncTask(name);
    setTask(t);
  }

  async function handleStop() {
    if (!name || !task) return;
    const t = await stopSyncTask(name);
    setTask(t);
  }

  async function handleDelete() {
    if (!name) return;
    await deleteSyncTask(name);
    navigate('/data');
  }

  if (loading) {
    return (
      <div className="page-container">
        <Breadcrumb
          items={[
            { label: '数据对接', to: '/data/sources' },
            { label: '同步任务', to: '/data/sources' },
            { label: '加载中…' },
          ]}
        />
        <div className="card">
          <SkeletonList rows={4} />
        </div>
      </div>
    );
  }

  if (error || !task) {
    return (
      <div className="page-container">
        <Breadcrumb items={[{ label: '数据对接', to: '/data/sources' }, { label: '同步任务' }]} />
        <button className="btn mt-2" onClick={() => navigate('/data')}>
          ← 返回
        </button>
        <p className="mt-4 text-error">{error || '同步任务不存在'}</p>
      </div>
    );
  }

  // Build columns from source_config for SyncModeSelector
  const sourceTable = task.source_config.table as string;
  const columns: ColumnInfo[] = task.source_config.incremental_column
    ? [
        {
          name: (task.source_config.incremental_column as string) || 'unknown',
          data_type: 'TIMESTAMP',
          nullable: false,
          is_primary_key: false,
          comment: '增量列',
        },
      ]
    : [];

  return (
    <div className="page-container">
      {/* Breadcrumb */}
      <Breadcrumb
        items={[
          { label: '数据对接', to: '/data/sources' },
          { label: '同步任务', to: '/data/sources' },
          { label: task.api_name },
        ]}
      />

      {/* Header */}
      <div className="mb-6">
        <SyncTaskCard
          task={task}
          onStart={handleStart}
          onStop={handleStop}
          onDelete={() => setConfirmDelete(true)}
        />
      </div>

      {/* Tabs */}
      <div className="view-toggle mb-4">
        {(['overview', 'config'] as const).map((t) => (
          <button
            key={t}
            className={cn('view-toggle-btn', tab === t && 'active')}
            onClick={() => setTab(t)}
          >
            {t === 'overview' ? '概览' : '配置'}
          </button>
        ))}
      </div>

      {/* Tab: Overview */}
      {tab === 'overview' && (
        <div className="card mb-4">
          <h3 className="mb-3 text-sm">同步配置摘要</h3>
          <table className="data-table">
            <tbody>
              <tr>
                <td className="w-[120px] text-text-muted">源数据源</td>
                <td>{task.data_source_id}</td>
              </tr>
              <tr>
                <td className="text-text-muted">源表</td>
                <td>
                  <code>{sourceTable || '-'}</code>
                </td>
              </tr>
              <tr>
                <td className="text-text-muted">目标 Dataset</td>
                <td>
                  <code>{task.target_dataset_api_name}</code>
                </td>
              </tr>
              <tr>
                <td className="text-text-muted">同步模式</td>
                <td>{task.sync_mode === 'incremental' ? '增量同步' : '全量快照'}</td>
              </tr>
              <tr>
                <td className="text-text-muted">事务类型</td>
                <td>{task.transaction_type === 'append' ? '追加写入' : '快照覆盖'}</td>
              </tr>
              {task.sync_mode === 'incremental' && (
                <tr>
                  <td className="text-text-muted">增量字段</td>
                  <td>
                    <code>{String(task.source_config.incremental_column || '-')}</code>
                  </td>
                </tr>
              )}
              {task.schedule && (
                <tr>
                  <td className="text-text-muted">调度</td>
                  <td>
                    {task.schedule.cron ? `Cron: ${task.schedule.cron}` : ''}
                    {task.schedule.interval_minutes
                      ? `每 ${task.schedule.interval_minutes} 分钟`
                      : ''}
                  </td>
                </tr>
              )}
              {task.last_run_at && (
                <tr>
                  <td className="text-text-muted">上次运行</td>
                  <td>{new Date(task.last_run_at).toLocaleString()}</td>
                </tr>
              )}
              <tr>
                <td className="text-text-muted">创建时间</td>
                <td>{new Date(task.created_at).toLocaleString()}</td>
              </tr>
            </tbody>
          </table>
        </div>
      )}

      {/* Tab: Config */}
      {tab === 'config' && (
        <div className="card">
          <h3 className="mb-3 text-sm">当前配置</h3>
          {columns.length > 0 && (
            <SyncModeSelector
              tableName={sourceTable || 'unknown'}
              columns={columns}
              value={{
                sync_mode: task.sync_mode as 'full_snapshot' | 'incremental',
                transaction_type: task.transaction_type as 'snapshot' | 'append',
                incremental_column: (task.source_config.incremental_column as string) || null,
                target_dataset: task.target_dataset_api_name,
              }}
              onChange={() => {}}
            />
          )}
          <div className="mt-4 rounded-md bg-bg p-3">
            <h4 className="mb-2 text-xs text-text-muted">原始配置 (source_config JSON)</h4>
            <pre className="max-h-[200px] overflow-auto font-mono text-[11px] text-text-secondary">
              {JSON.stringify(task.source_config, null, 2)}
            </pre>
          </div>
        </div>
      )}

      {/* Delete Confirm */}
      {confirmDelete && (
        <ConfirmDialog
          severity="LOW"
          title={`删除同步任务 "${task.api_name}"`}
          message="此操作不可撤销，关联的 SeaTunnel 流水线将一并删除。"
          onConfirm={handleDelete}
          onCancel={() => setConfirmDelete(false)}
        />
      )}
    </div>
  );
}
