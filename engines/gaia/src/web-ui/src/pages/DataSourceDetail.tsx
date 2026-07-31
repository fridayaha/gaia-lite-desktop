import { useState, useEffect, useRef } from 'react';
import { useParams, useNavigate } from 'react-router-dom';
import { cn } from '../lib/cn';
import { formatError } from '../lib/formatError';
import { getDataSource, analyzeImpact, listDatasets } from '../api/client';
import { Breadcrumb } from '../components/Breadcrumb';
import { DataSourceCard } from '../components/DataSourceCard';
import { SyncTaskCard } from '../components/SyncTaskCard';
import { ExplorerView } from '../components/ExplorerView';
import { DataSourceForm } from '../components/DataSourceForm';
import { RegisterVirtualTableDialog } from '../components/RegisterVirtualTableDialog';
import { SkeletonList } from '../components/Skeleton';
import type { SchemaNode } from '../components/ExplorerView';
import { ConfirmDialog } from '../components/ConfirmDialog';
import { ToastView } from '../components/ToastView';
import { useDataSource } from '../hooks/useDataSource';
import type { ExploreError } from '../hooks/useDataSource';
import { useToast } from '../hooks/useToast';
import { useAllowedActions } from '../hooks/useAllowedActions';
import { PermissionGate, AccessDecisionPanel } from '../components/permission';
import {
  checkAccess,
  createRoleAssignment,
  listRoleAssignments,
  type CheckAccessResult,
  type RoleAssignment,
} from '../api/permission';
import type { DataSource, SyncTask, ImpactAnalysis, ImpactItem } from '../types';

// 数据源暴露的权限动作（与后端 action_registry DATASOURCE 对齐）。
const DS_VIEW = 'datasource:view';
const DS_EDIT = 'datasource:edit';
const DS_DELETE = 'datasource:delete';

export function DataSourceDetail() {
  const { name } = useParams<{ name: string }>();
  const navigate = useNavigate();

  const [ds, setDs] = useState<DataSource | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [tab, setTab] = useState<'explore' | 'sync' | 'access' | 'settings'>('explore');
  // 记录已访问过的 tab，避免来回切时重复 fetch。已访问的 tab 数据保留
  // 在 hook 状态里，切回时直接渲染（不需重拉）。
  const visitedTabsRef = useRef<Set<'explore' | 'sync' | 'access' | 'settings'>>(
    new Set(['explore']),
  );

  // Ship-the-decision：后端批量返回该数据源的 allowedActions（设计 §8.2）。
  // 三道闸门的 Render Gate——前端只渲染状态，不推导规则。
  const dsName = name ?? '';
  const { decisions, loading: permLoading } = useAllowedActions('DATASOURCE', dsName ? [dsName] : []);
  // 就地展开被拒 action 的五层校验详情（research §2.3：拒绝不是死路，是就地解释）。
  const [explainAction, setExplainAction] = useState<string | null>(null);
  const [explainResult, setExplainResult] = useState<CheckAccessResult | null>(null);
  const [explainLoading, setExplainLoading] = useState(false);
  // 就近角色授予（design §7.3 / §8.4）：在资源上下文就地管理「谁能访问」。
  const [roleAssignments, setRoleAssignments] = useState<RoleAssignment[]>([]);
  const [grantGroupId, setGrantGroupId] = useState('');
  const [grantRole, setGrantRole] = useState('VIEWER');
  const [granting, setGranting] = useState(false);
  const [grantMsg, setGrantMsg] = useState<string | null>(null);

  const [editFormOpen, setEditFormOpen] = useState(false);
  const [confirmDlg, setConfirmDlg] = useState<{
    targetApiName: string;
    targetType: string;
    displayName: string;
    onConfirm: () => void;
  } | null>(null);
  const [impact, setImpact] = useState<ImpactAnalysis | null>(null);
  // F0: register-virtual-table dialog target
  const [registerTarget, setRegisterTarget] = useState<{
    database: string;
    table: string;
  } | null>(null);
  /** 已有数据集 api_name 列表，供同步目标选择器（P1 交叉互通）。 */
  const [existingDatasets, setExistingDatasets] = useState<string[]>([]);
  const { toast, show: showToast, dismiss } = useToast();

  const dsHook = useDataSource();

  // P3：AbortController 应对 React StrictMode dev 双 mount——
  // 第一次 effect 被 cleanup abort，第二次才真正执行。
  useEffect(() => {
    const controller = new AbortController();
    async function loadAll() {
      if (!name) return;
      setLoading(true);
      setError(null);
      try {
        const dsData = await getDataSource(name);
        if (controller.signal.aborted) return;
        setDs(dsData);
        // P0：mount 只加载主实体 + 默认 explore tab。sync tab 的 refresh
        // 懒加载到首次激活时（见下方 handleTabChange），避免打开详情页就
        // 触发 N 次 SeaTunnel refresh 风暴。但 tab 计数需要数据，这里调
        // 轻量的 loadSyncTasks（纯 PG 查询，零 SeaTunnel 调用）填充计数。
        await dsHook.fetchExplore(name);
        await dsHook.loadSyncTasks(name);
        // 预加载已有数据集列表，供同步目标选择器（P1）
        try {
          const datasets = await listDatasets();
          if (controller.signal.aborted) return;
          setExistingDatasets(datasets.map((d) => d.api_name));
        } catch {
          /* 非致命：选择器退化为纯文本输入 */
        }
      } catch (err: unknown) {
        if (controller.signal.aborted) return;
        setError(formatError(err, '数据源不存在或加载失败'));
      } finally {
        if (!controller.signal.aborted) setLoading(false);
      }
    }
    loadAll();
    return () => controller.abort();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [name]);

  // P0：tab 懒加载——sync tab 首次激活时才 fetch 同步任务列表。
  // 已访问过的 tab 不重拉（数据在 hook 状态里保留）。
  useEffect(() => {
    if (tab !== 'sync') return;
    if (visitedTabsRef.current.has('sync')) return;
    visitedTabsRef.current.add('sync');
    if (name) dsHook.fetchSyncTasksFor(name);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [tab, name]);

  async function handleStartSync(task: SyncTask) {
    try {
      await dsHook.startSync(task);
      showToast('同步已启动', 'success');
    } catch (err) {
      showToast('启动失败: ' + formatError(err), 'error');
    }
  }

  async function handleStopSync(task: SyncTask) {
    try {
      await dsHook.stopSync(task);
      showToast('同步已停止', 'success');
    } catch (err) {
      showToast('停止失败: ' + formatError(err), 'error');
    }
  }

  async function handleDeleteSyncTask(task: SyncTask) {
    try {
      const imp = await analyzeImpact(task.api_name, 'sync_task', 'delete');
      setImpact(imp);
    } catch {
      setImpact(null);
    }
    setConfirmDlg({
      targetApiName: task.api_name,
      targetType: 'sync_task',
      displayName: task.api_name,
      onConfirm: async () => {
        try {
          await dsHook.removeSync(task);
          showToast('同步任务已删除', 'success');
        } catch (err) {
          showToast('删除失败: ' + formatError(err), 'error');
        }
        setConfirmDlg(null);
      },
    });
  }

  async function loadRoleAssignments() {
    try {
      const list = await listRoleAssignments();
      setRoleAssignments(list);
    } catch {
      setRoleAssignments([]);
    }
  }

  async function handleGrantRole() {
    if (!grantGroupId.trim()) {
      setGrantMsg('请输入 Group ID');
      return;
    }
    setGranting(true);
    setGrantMsg(null);
    try {
      await createRoleAssignment({
        group_id: grantGroupId.trim(),
        role_name: grantRole,
        scope_type: 'PROJECT',
        scope_id: null,
      });
      setGrantMsg('授予成功');
      setGrantGroupId('');
      await loadRoleAssignments();
    } catch (err) {
      setGrantMsg('授予失败: ' + formatError(err));
    } finally {
      setGranting(false);
    }
  }

  async function handleRevokeRole(id: string) {
    try {
      const { deleteRoleAssignment } = await import('../api/permission');
      await deleteRoleAssignment(id);
      await loadRoleAssignments();
    } catch (err) {
      showToast('撤销失败: ' + formatError(err), 'error');
    }
  }

  async function handleExplain(action: string) {
    // 切换：点击已展开的同一 action 则收起。
    if (explainAction === action) {
      setExplainAction(null);
      setExplainResult(null);
      return;
    }
    setExplainAction(action);
    setExplainResult(null);
    setExplainLoading(true);
    try {
      const r = await checkAccess('DATASOURCE', dsName, action);
      setExplainResult(r);
    } catch {
      // 静默失败——面板状态由 explainLoading 兜底
    } finally {
      setExplainLoading(false);
    }
  }

  if (loading) {
    return (
      <div className="page-container">
        <Breadcrumb items={[{ label: '数据对接', to: '/data/sources' }, { label: '加载中…' }]} />
        <div className="card">
          <SkeletonList rows={4} />
        </div>
      </div>
    );
  }

  if (error || !ds) {
    return (
      <div className="page-container">
        <Breadcrumb items={[{ label: '数据对接', to: '/data/sources' }, { label: '数据源' }]} />
        <div className="mb-4 flex items-center gap-3">
          <button className="btn" onClick={() => navigate('/data')}>
            ← 返回
          </button>
          <h1>数据源不存在</h1>
        </div>
        <p className="text-error">{error}</p>
      </div>
    );
  }

  const schemaNodes: SchemaNode[] =
    dsHook.explore && dsHook.explore.tables.length > 0
      ? [{ schema_name: dsHook.explore.database || 'public', tables: dsHook.explore.tables }]
      : [];

  return (
    <div className="page-container">
      {/* Breadcrumb */}
      <Breadcrumb
        items={[{ label: '数据对接', to: '/data/sources' }, { label: ds.display_name }]}
      />

      <DataSourceCard ds={ds} variant="detail">
        {/* Tabs */}
        <div className="view-toggle mt-4">
          {(['explore', 'sync', 'access', 'settings'] as const).map((t) => (
            <button
              key={t}
              className={cn('view-toggle-btn', tab === t && 'active')}
              onClick={() => setTab(t)}
            >
              {t === 'explore'
                ? '浏览 Schema'
                : t === 'sync'
                  ? `同步任务 (${dsHook.syncTasks.length})`
                  : t === 'access'
                    ? '访问控制'
                    : '设置'}
            </button>
          ))}
        </div>

        {/* Tab: Explore */}
        {tab === 'explore' &&
          (dsHook.exploreLoading ? (
            <div className="p-6 text-center text-[13px] text-text-muted">加载 Schema 中…</div>
          ) : !dsHook.explore ? (
            <ExploreErrorView
              error={dsHook.exploreError}
              onRetry={() => name && dsHook.fetchExplore(name)}
            />
          ) : schemaNodes.length > 0 ? (
            <ExplorerView
              schemas={schemaNodes}
              columnMap={dsHook.columnDetails}
              existingDatasets={existingDatasets}
              onRefreshSchema={() => name && dsHook.fetchExplore(name)}
              refreshLoading={dsHook.exploreLoading}
              onCreateSync={async (tableName, config) => {
                if (!name) return;
                // 用源表的中文名/注释作数据集显示名，没有注释时退化成表名。
                const tableComment = dsHook.columnDetails[tableName]?.comment;
                await dsHook.createSyncTask(name, tableName, config, tableComment || tableName);
                showToast(`同步任务 ${config.target_dataset || tableName + '_raw'} 已创建`, 'success');
              }}
              onTableClick={(tableName: string) => {
                if (!name || !dsHook.explore) return;
                const db = dsHook.explore.database || 'public';
                dsHook.fetchColumns(name, db, tableName);
                dsHook.fetchSample(name, db, tableName);
              }}
              onRegisterVirtualTable={(tableName: string) => {
                const db = dsHook.explore?.database || 'public';
                setRegisterTarget({ database: db, table: tableName });
              }}
              sampleData={dsHook.sample}
              sampleLoading={dsHook.sampleLoading}
              sampleError={dsHook.sampleError}
            />
          ) : (
            <div className="p-6 text-center text-[13px]">
              <span className="text-text-muted">📭 已连接但未找到表</span>
              <div className="mt-1.5 text-xs text-text-muted">
                数据库「{dsHook.explore?.database || 'public'}」中没有表，或 Schema 名不匹配
              </div>
            </div>
          ))}

        {/* Tab: Sync Tasks */}
        {tab === 'sync' && (
          <div className="mt-2">
            {dsHook.syncTasks.length === 0 ? (
              <p className="text-xs text-text-muted">暂无同步任务</p>
            ) : (
              dsHook.syncTasks.map((task) => (
                <SyncTaskCard
                  key={task.id}
                  task={task}
                  onStart={() => handleStartSync(task)}
                  onStop={() => handleStopSync(task)}
                  onDelete={() => handleDeleteSyncTask(task)}
                  onClick={() => navigate(`/data/syncs/${task.api_name}`)}
                />
              ))
            )}
          </div>
        )}

        {/* Tab: Access (就近管理 — design §8.4 / research §1.1) */}
        {tab === 'access' && (
          <div className="mt-2 rounded-md bg-bg p-4">
            <h4 className="mb-3 text-[13px]">访问控制</h4>
            <p className="mb-4 text-xs text-text-muted">
              当前用户对此数据源的权限决策（由后端五层校验实时返回）。
            </p>
            {permLoading ? (
              <div className="text-xs text-text-muted">加载权限中…</div>
            ) : (
              <div className="flex flex-col gap-2">
                {([
                  { action: DS_VIEW, label: '查看' },
                  { action: DS_EDIT, label: '编辑' },
                  { action: DS_DELETE, label: '删除' },
                ] as const).map(({ action, label }) => {
                  const allowed = decisions[dsName]?.allowedActions.includes(action) ?? false;
                  const reason = decisions[dsName]?.disabledReasons[action] ?? '';
                  const isOpen = explainAction === action;
                  return (
                    <div key={action} className="flex flex-col gap-1">
                      <div className="flex items-center justify-between rounded border border-border bg-surface px-3 py-2">
                        <div className="flex items-center gap-2">
                          <code className="text-xs text-text-secondary">{action}</code>
                          <span className="text-xs text-text-muted">{label}</span>
                        </div>
                        {allowed ? (
                          <span className="rounded bg-success/15 px-2 py-0.5 text-xs text-success">
                            ✓ 允许
                          </span>
                        ) : (
                          <div className="flex items-center gap-2">
                            <span
                              className="rounded bg-error/10 px-2 py-0.5 text-xs text-error"
                              title={reason}
                            >
                              ✗ 拒绝
                            </span>
                            <button
                              onClick={() => handleExplain(action)}
                              className="text-xs text-accent hover:underline"
                            >
                              {isOpen ? '收起' : '为什么？'}
                            </button>
                          </div>
                        )}
                      </div>
                      {isOpen && (
                        <div className="rounded border border-border bg-bg p-3">
                          {explainLoading ? (
                            <div className="text-xs text-text-muted">加载校验详情…</div>
                          ) : explainResult ? (
                            <AccessDecisionPanel result={explainResult} />
                          ) : (
                            <div className="text-xs text-text-muted">无法获取校验详情</div>
                          )}
                        </div>
                      )}
                    </div>
                  );
                })}
              </div>
            )}
            <div className="mt-4 border-t border-border pt-3">
              <h5 className="mb-2 text-xs font-medium text-text-muted">角色授予（就近管理）</h5>
              <p className="mb-2 text-[11px] text-text-muted">
                授予 Group 角色以控制访问。需 role:manage 权限。
              </p>
              <div className="mb-3 flex flex-wrap items-end gap-2">
                <label className="block">
                  <span className="text-[11px] text-text-muted">Group ID</span>
                  <input
                    type="text"
                    value={grantGroupId}
                    onChange={(e) => setGrantGroupId(e.target.value)}
                    placeholder="group-uuid"
                    className="mt-0.5 w-48 px-2 py-1 rounded border border-border bg-bg text-xs"
                  />
                </label>
                <label className="block">
                  <span className="text-[11px] text-text-muted">角色</span>
                  <select
                    value={grantRole}
                    onChange={(e) => setGrantRole(e.target.value)}
                    className="mt-0.5 px-2 py-1 rounded border border-border bg-bg text-xs"
                  >
                    {['VIEWER', 'EDITOR', 'OWNER', 'DISCOVERER'].map((r) => (
                      <option key={r} value={r}>{r}</option>
                    ))}
                  </select>
                </label>
                <button
                  onClick={handleGrantRole}
                  disabled={granting}
                  className="btn btn-xs btn-primary disabled:opacity-50"
                >
                  {granting ? '授予中…' : '授予'}
                </button>
                {grantMsg && <span className="text-[11px] text-text-muted">{grantMsg}</span>}
              </div>
              {roleAssignments.length > 0 && (
                <div className="space-y-1">
                  {roleAssignments.map((ra) => (
                    <div
                      key={ra.id}
                      className="flex items-center justify-between rounded border border-border bg-surface px-2 py-1 text-xs"
                    >
                      <span>
                        <code className="text-text-secondary">{ra.role_name}</code>
                        {' → group '}
                        <code className="text-text-secondary">{ra.group_id.slice(0, 8)}…</code>
                      </span>
                      <button
                        onClick={() => handleRevokeRole(ra.id)}
                        className="text-error hover:underline text-[11px]"
                      >
                        撤销
                      </button>
                    </div>
                  ))}
                </div>
              )}
              <button
                onClick={loadRoleAssignments}
                className="mt-2 text-[11px] text-accent hover:underline"
              >
                加载已有授予
              </button>
            </div>
            <div className="mt-4 border-t border-border pt-3 text-xs text-text-muted">
              资源标识：DATASOURCE / <code className="text-text-secondary">{dsName}</code>
            </div>
          </div>
        )}

        {/* Tab: Settings */}
        {tab === 'settings' && (
          <div className="mt-2 rounded-md bg-bg p-4">
            <div className="mb-3 flex items-center justify-between">
              <h4 className="text-[13px]">连接配置</h4>
              <button className="btn btn-sm" onClick={() => setEditFormOpen(true)}>
                编辑连接配置
              </button>
            </div>
            <PermissionGate
              action={DS_VIEW}
              resourceId={dsName}
              decisions={decisions}
              mode="disable"
              fallback={<p className="text-xs text-text-muted">无权查看连接配置</p>}
            >
              <div className="flex flex-col gap-2 font-mono text-xs">
                <div>
                  类型: <span className="text-text-secondary">{ds.connector_type}</span>
                </div>
                <div>
                  Gravitino Catalog:{' '}
                  <span className="text-text-secondary">{ds.gravitino_catalog_name}</span>
                </div>
                {Object.entries(ds.connector_config).map(([k, v]) => (
                  <div key={k}>
                    {k}:{' '}
                    <span className="text-text-secondary">
                      {k.includes('password') || k.includes('secret') ? '••••' : v}
                    </span>
                  </div>
                ))}
              </div>
            </PermissionGate>
          </div>
        )}
      </DataSourceCard>

      {confirmDlg && impact && (
        <ConfirmDialog
          severity={impact.severity as 'LOW' | 'MEDIUM' | 'HIGH'}
          title={
            '删除' +
            (confirmDlg.targetType === 'sync_task' ? '同步任务' : '数据源') +
            ' "' +
            confirmDlg.displayName +
            '"'
          }
          message="此操作不可撤销。"
          impacts={impact.impacts as ImpactItem[]}
          requireName={impact.severity === 'HIGH' ? confirmDlg.displayName : undefined}
          onConfirm={confirmDlg.onConfirm}
          onCancel={() => {
            setConfirmDlg(null);
            setImpact(null);
          }}
        />
      )}

      {/* 编辑数据源弹窗 */}
      {editFormOpen && ds && (
        <DataSourceForm
          initialData={ds}
          onUpdated={(updated) => {
            setDs(updated);
            setEditFormOpen(false);
            showToast('数据源已更新', 'success');
          }}
          onCancel={() => setEditFormOpen(false)}
        />
      )}

      {/* F0: register external table as a Virtual Table dataset */}
      {registerTarget && name && (
        <RegisterVirtualTableDialog
          datasourceApiName={name}
          datasourceDisplayName={ds.display_name}
          database={registerTarget.database}
          table={registerTarget.table}
          onClose={() => setRegisterTarget(null)}
          onRegistered={() => {
            // Could navigate to dataset detail; for now just toast (dialog handles toast).
          }}
        />
      )}

      <ToastView toast={toast} onDismiss={dismiss} />
    </div>
  );
}

/**
 * 探索失败提示：根据后端错误码区分根因，给出可操作的提示而非笼统的“服务不可用”。
 *
 * - DATASOURCE_UNREACHABLE (502)：数据源本身连不上（外部 DB 宕机/网络不通）。
 *   提示用户检查数据源服务，而非 Gravitino/Trino。
 * - CATALOG_NOT_REGISTERED (502)：数据源的 Gravitino catalog 丢失（引擎重建/升级后常见）。
 *   后端会自动重注册并重试；若仍失败，提示用户点「重试」触发再次自愈。
 * - TRINO_UNAVAILABLE (503)：查询引擎服务不可用。提示检查 Gravitino/Trino。
 * - 其他/未知：退化为通用提示 + 后端原始消息首句。
 */
function ExploreErrorView({ error, onRetry }: { error: ExploreError | null; onRetry: () => void }) {
  let title = '⚠ 探索失败';
  let hint = '请确认 Gravitino 和 Trino 服务可用';
  if (error?.code === 'DATASOURCE_UNREACHABLE') {
    title = '⚠ 无法连接到数据源';
    hint = '数据源服务未运行或网络不可达，请确认数据源实例在线后再试';
  } else if (error?.code === 'CATALOG_NOT_REGISTERED') {
    title = '⚠ 数据源连接已失效';
    hint = '引擎重建后连接信息丢失，点击「重试」可自动恢复';
  } else if (error?.code === 'TRINO_UNAVAILABLE') {
    title = '⚠ 查询引擎不可用';
    hint = 'Trino 服务无响应，请确认 Gravitino / Trino 容器正常运行';
  } else if (error?.message) {
    hint = error.message;
  }
  return (
    <div className="p-6 text-center text-[13px]">
      <span className="text-error">{title}</span>
      <div className="mt-1.5 text-text-muted">{hint}</div>
      <div className="mt-2">
        <button className="btn btn-xs" onClick={onRetry}>
          重试
        </button>
      </div>
    </div>
  );
}
