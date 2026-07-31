import { useEffect, useState } from 'react';
import { getObjectType, listLinkTypes, listActionTypes, listDatasets, updateObjectTypeCapabilities } from '../api/client';
import { cn } from '../lib/cn';
import { formatError } from '../lib/formatError';
import { SkeletonDetail } from './Skeleton';
import { DatasetLinkDialog } from './DatasetLinkDialog';
import { CreateLinkDialog } from './CreateLinkDialog';
import { ExecuteActionDialog } from './ExecuteActionDialog';
import { ActionTypeEditor } from './ActionTypeEditor';
import { PermissionGate, AccessDecisionPanel } from './permission';
import { useAllowedActions } from '../hooks/useAllowedActions';
import {
  checkAccess,
  createRoleAssignment,
  listRoleAssignments,
  deleteRoleAssignment,
  assignMarking,
  revokeMarking,
  listMarkings,
  type CheckAccessResult,
  type RoleAssignment,
  type Marking,
} from '../api/permission';
import type {
  ObjectType,
  ObjectTypeCapabilities,
  ObjectTypeSummary,
  LinkTypeDef,
  ActionTypeRecord,
  DatasetGovernance,
} from '../types';

interface ObjectDetailPanelProps {
  ontologyName: string;
  objectType: ObjectTypeSummary;
  onEdit: () => void;
  onDelete: () => void;
  onClose: () => void;
  /** P1 (ADR-011): refresh callback invoked after an Action is applied, so
   * the parent view re-fetches object data (read-your-writes). */
  onActionApplied?: () => void;
}

/**
 * 对象详情面板（右侧）。选中对象/节点时展示属性、关系、动作。
 * 复用 ColumnList 不合适（语义不同），这里用轻量内联渲染，遵循组件复用原则：
 * 属性/关系/动作三段式，与设计文档 frontend-data-layer-design 一致。
 */
export function ObjectDetailPanel({
  ontologyName,
  objectType,
  onEdit,
  onDelete,
  onClose,
  onActionApplied,
}: ObjectDetailPanelProps) {
  const [detail, setDetail] = useState<ObjectType | null>(null);
  const [links, setLinks] = useState<LinkTypeDef[]>([]);
  const [actions, setActions] = useState<ActionTypeRecord[]>([]);
  const [datasets, setDatasets] = useState<DatasetGovernance[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [showLinkDialog, setShowLinkDialog] = useState(false);
  const [showCreateLink, setShowCreateLink] = useState(false);
  const [execAction, setExecAction] = useState<ActionTypeRecord | null>(null);
  const [editAction, setEditAction] = useState<{
    mode: 'create' | 'edit';
    apiName?: string;
  } | null>(null);
  const [reloadKey, setReloadKey] = useState(0);

  // ── Tab 分层（design §8.4 资源详情面板范式）：概览 / 定义 / 能力 / 访问控制 ──
  const [tab, setTab] = useState<'overview' | 'definition' | 'capabilities' | 'access'>('definition');

  // ── 权限决策（ship-the-decision，design §8.2）──
  const otId = objectType.api_name;
  const { decisions } = useAllowedActions('OBJECT_TYPE', [otId]);

  // ── 访问控制 tab：角色授予 + 标记打标 + 被拒解释 ──
  const [roleAssignments, setRoleAssignments] = useState<RoleAssignment[]>([]);
  const [grantGroupId, setGrantGroupId] = useState('');
  const [grantRole, setGrantRole] = useState('VIEWER');
  const [granting, setGranting] = useState(false);
  const [grantMsg, setGrantMsg] = useState<string | null>(null);
  const [markings, setMarkings] = useState<Marking[]>([]);
  const [appliedMarkings, setAppliedMarkings] = useState<string[]>([]);
  const [markingToApply, setMarkingToApply] = useState('');
  const [explainAction, setExplainAction] = useState<string | null>(null);
  const [explainResult, setExplainResult] = useState<CheckAccessResult | null>(null);
  const [explainLoading, setExplainLoading] = useState(false);

  useEffect(() => {
    let cancelled = false;
    async function load() {
      setLoading(true);
      setError(null);
      try {
        const [ot, lks, acts, dss] = await Promise.all([
          getObjectType(ontologyName, objectType.api_name),
          listLinkTypes(ontologyName),
          listActionTypes(ontologyName).catch(() => [] as ActionTypeRecord[]),
          listDatasets().catch(() => [] as DatasetGovernance[]),
        ]);
        if (cancelled) return;
        setDetail(ot);
        setLinks(lks);
        setActions(acts);
        setDatasets(dss);
      } catch (err) {
        if (!cancelled) setError(formatError(err, '加载对象详情失败'));
      } finally {
        if (!cancelled) setLoading(false);
      }
    }
    load();
    return () => {
      cancelled = true;
    };
  }, [ontologyName, objectType.api_name, objectType.id, reloadKey]);

  const outgoingLinks = links.filter((l) => l.source_object_type_id === objectType.id);
  const incomingLinks = links.filter((l) => l.target_object_type_id === objectType.id);
  const objectActions = actions.filter((a) => a.affected_object_type_id === objectType.id);

  // F4: dataset binding status
  const boundDatasetApiName =
    detail?.properties.find((p) => p.backing_mapping)?.backing_mapping?.dataset_api_name ?? '';
  const boundDataset = datasets.find((d) => d.api_name === boundDatasetApiName) ?? null;
  const mappedProps = detail?.properties.filter((p) => p.backing_mapping) ?? [];
  const isVirtual = objectType.storage_type === 'VIRTUAL';

  // ── 访问控制 tab 处理函数 ──
  async function loadAccessInfo() {
    try {
      const [ras, marks] = await Promise.all([
        listRoleAssignments().catch(() => []),
        listMarkings().catch(() => []),
      ]);
      setRoleAssignments(ras);
      setMarkings(marks);
    } catch {
      /* 非致命 */
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
      await loadAccessInfo();
    } catch (err) {
      setGrantMsg(formatError(err, '授予失败'));
    } finally {
      setGranting(false);
    }
  }

  async function handleRevokeRole(id: string) {
    try {
      await deleteRoleAssignment(id);
      await loadAccessInfo();
    } catch (err) {
      setGrantMsg(formatError(err, '撤销失败'));
    }
  }

  async function handleApplyMarking() {
    if (!markingToApply) return;
    try {
      await assignMarking('OBJECT_TYPE', otId, markingToApply);
      setAppliedMarkings((m) => [...m, markingToApply]);
      setMarkingToApply('');
    } catch (err) {
      setGrantMsg(formatError(err, '打标失败'));
    }
  }

  async function handleRevokeMarking(markingId: string) {
    try {
      await revokeMarking('OBJECT_TYPE', otId, markingId);
      setAppliedMarkings((m) => m.filter((x) => x !== markingId));
    } catch (err) {
      setGrantMsg(formatError(err, '撤销标记失败'));
    }
  }

  async function handleExplain(action: string) {
    if (explainAction === action) {
      setExplainAction(null);
      setExplainResult(null);
      return;
    }
    setExplainAction(action);
    setExplainResult(null);
    setExplainLoading(true);
    try {
      const r = await checkAccess('OBJECT_TYPE', otId, action);
      setExplainResult(r);
    } catch {
      /* 静默 */
    } finally {
      setExplainLoading(false);
    }
  }

  // 切到访问控制 tab 时加载角色授予 + 标记
  useEffect(() => {
    if (tab === 'access') loadAccessInfo();
  }, [tab]);

  return (
    <aside
      className="flex w-[340px] shrink-0 flex-col border-l border-border bg-sidebar"
      style={{ animation: 'drawer-in 0.2s ease' }}
    >
      {/* Header */}
      <div className="flex items-start justify-between border-b border-border px-4 py-3">
        <div className="min-w-0">
          <div className="flex items-center gap-2">
            <span className="truncate text-sm font-semibold text-text">
              {objectType.display_name}
            </span>
            <span
              className={cn(
                'object-card-status',
                `status-${objectType.storage_type === 'VIRTUAL' ? 'experimental' : 'active'}`,
              )}
            >
              {objectType.storage_type === 'VIRTUAL' ? '虚拟' : '托管'}
            </span>
          </div>
          <code className="font-mono text-[11px] text-text-muted">{objectType.api_name}</code>
        </div>
        <button className="btn btn-xs" aria-label="关闭详情" onClick={onClose}>
          ✕
        </button>
      </div>

      {/* Tab 切换（资源详情面板范式，design §8.4）*/}
      <div className="flex border-b border-border">
        {([
          { id: 'overview', label: '概览' },
          { id: 'definition', label: '定义' },
          { id: 'capabilities', label: '能力' },
          { id: 'access', label: '访问控制' },
        ] as const).map((t) => (
          <button
            key={t.id}
            className={cn(
              'flex-1 px-3 py-2 text-xs font-medium border-b-2 transition-colors',
              tab === t.id
                ? 'border-accent text-accent'
                : 'border-transparent text-text-muted hover:text-text',
            )}
            onClick={() => setTab(t.id)}
          >
            {t.label}
          </button>
        ))}
      </div>

      <div className="flex-1 overflow-y-auto p-4">
        {loading ? (
          <SkeletonDetail />
        ) : error ? (
          <div className="error-box">{error}</div>
        ) : detail ? (
          <>
            {/* ── 概览 tab（资源元信息 + 所属 Project，design §8.4）── */}
            {tab === 'overview' && (
              <div className="space-y-4">
                {detail.description && (
                  <p className="text-xs text-text-secondary">{detail.description}</p>
                )}
                <div className="grid grid-cols-1 gap-2 text-xs">
                  <div className="flex justify-between border-b border-border/50 py-1.5">
                    <span className="text-text-muted">API 名称</span>
                    <code className="font-mono text-text">{detail.api_name}</code>
                  </div>
                  <div className="flex justify-between border-b border-border/50 py-1.5">
                    <span className="text-text-muted">存储类型</span>
                    <span className="text-text">{detail.storage_type === 'VIRTUAL' ? '虚拟表' : '托管表'}</span>
                  </div>
                  <div className="flex justify-between border-b border-border/50 py-1.5">
                    <span className="text-text-muted">主键</span>
                    <code className="font-mono text-text">{detail.primary_key}</code>
                  </div>
                  <div className="flex justify-between border-b border-border/50 py-1.5">
                    <span className="text-text-muted">标题字段</span>
                    <code className="font-mono text-text">{detail.title_property}</code>
                  </div>
                  <div className="flex justify-between border-b border-border/50 py-1.5">
                    <span className="text-text-muted">所属本体</span>
                    <code className="font-mono text-text">{ontologyName}</code>
                  </div>
                  {/* 所属 Project（选项 A：真实归属）*/}
                  <div className="flex justify-between border-b border-border/50 py-1.5">
                    <span className="text-text-muted">所属 Project</span>
                    <code className="font-mono text-text text-[11px]">
                      {objectType.project_id ? objectType.project_id.slice(0, 8) + '…' : '—'}
                    </code>
                  </div>
                </div>
                <div className="rounded-md bg-info/5 border border-info/20 px-3 py-2 text-[11px] text-text-muted">
                  💡 定义类资源的权限从所属 Project 继承。
                  未来可在创建时显式指定 Project 实现细粒度协作。
                </div>
              </div>
            )}

            {/* ── 访问控制 tab（权限决策 + 角色授予 + 标记，就近管理）── */}
            {tab === 'access' && (
              <div className="space-y-4">
                {/* 当前用户权限决策 */}
                <div>
                  <h5 className="mb-2 text-xs font-semibold uppercase tracking-wide text-text-secondary">
                    当前用户权限
                  </h5>
                  {([
                    { action: 'object_type:view', label: '查看定义' },
                    { action: 'object_type:edit', label: '编辑定义' },
                    { action: 'object_type:delete', label: '删除' },
                    { action: 'object:view', label: '查看数据' },
                    { action: 'object:write', label: '写入数据' },
                  ] as const).map(({ action, label }) => {
                    const allowed = decisions[otId]?.allowedActions.includes(action) ?? false;
                    const reason = decisions[otId]?.disabledReasons[action] ?? '';
                    const isOpen = explainAction === action;
                    return (
                      <div key={action} className="mb-1.5">
                        <div className="flex items-center justify-between rounded border border-border bg-surface px-2 py-1.5">
                          <div className="flex items-center gap-1.5">
                            <code className="text-[10px] text-text-muted">{action}</code>
                            <span className="text-[11px] text-text-muted">{label}</span>
                          </div>
                          <div className="flex items-center gap-1.5">
                            {allowed ? (
                              <span className="rounded bg-success/15 px-1.5 py-0.5 text-[10px] text-success">✓</span>
                            ) : (
                              <span className="rounded bg-error/10 px-1.5 py-0.5 text-[10px] text-error" title={reason}>✗</span>
                            )}
                            {!allowed && (
                              <button onClick={() => handleExplain(action)} className="text-[10px] text-accent hover:underline">
                                {isOpen ? '收起' : '为什么？'}
                              </button>
                            )}
                          </div>
                        </div>
                        {isOpen && (
                          <div className="mt-1 rounded border border-border bg-bg p-2">
                            {explainLoading ? (
                              <div className="text-[11px] text-text-muted">加载中…</div>
                            ) : explainResult ? (
                              <AccessDecisionPanel result={explainResult} />
                            ) : (
                              <div className="text-[11px] text-text-muted">无法获取</div>
                            )}
                          </div>
                        )}
                      </div>
                    );
                  })}
                </div>

                {/* 角色授予（就近管理，design §8.4）*/}
                <div>
                  <h5 className="mb-2 text-xs font-semibold uppercase tracking-wide text-text-secondary">
                    角色授予
                  </h5>
                  <div className="flex flex-wrap items-end gap-1.5">
                    <input
                      type="text"
                      value={grantGroupId}
                      onChange={(e) => setGrantGroupId(e.target.value)}
                      placeholder="Group ID"
                      className="w-28 px-1.5 py-1 rounded border border-border bg-bg text-[11px]"
                    />
                    <select
                      value={grantRole}
                      onChange={(e) => setGrantRole(e.target.value)}
                      className="px-1.5 py-1 rounded border border-border bg-bg text-[11px]"
                    >
                      {['VIEWER', 'EDITOR', 'OWNER', 'DISCOVERER'].map((r) => (
                        <option key={r} value={r}>{r}</option>
                      ))}
                    </select>
                    <button onClick={handleGrantRole} disabled={granting} className="btn btn-xs btn-primary disabled:opacity-50">
                      {granting ? '…' : '授予'}
                    </button>
                  </div>
                  {grantMsg && <p className="mt-1 text-[10px] text-text-muted">{grantMsg}</p>}
                  {roleAssignments.length > 0 && (
                    <div className="mt-1.5 space-y-0.5">
                      {roleAssignments.slice(0, 5).map((ra) => (
                        <div key={ra.id} className="flex items-center justify-between text-[10px]">
                          <span><code>{ra.role_name}</code> → {ra.group_id.slice(0, 8)}…</span>
                          <button onClick={() => handleRevokeRole(ra.id)} className="text-error hover:underline">撤销</button>
                        </div>
                      ))}
                    </div>
                  )}
                </div>

                {/* 标记打标（MAC 就地化，B2 操作层）*/}
                <div>
                  <h5 className="mb-2 text-xs font-semibold uppercase tracking-wide text-text-secondary">
                    标记
                  </h5>
                  {appliedMarkings.length > 0 && (
                    <div className="mb-1.5 space-y-0.5">
                      {appliedMarkings.map((mid) => {
                        const m = markings.find((x) => x.id === mid);
                        return (
                          <div key={mid} className="flex items-center justify-between text-[10px]">
                            <span><code>{m?.name ?? mid}</code></span>
                            <button onClick={() => handleRevokeMarking(mid)} className="text-error hover:underline">移除</button>
                          </div>
                        );
                      })}
                    </div>
                  )}
                  <div className="flex items-end gap-1.5">
                    <select
                      value={markingToApply}
                      onChange={(e) => setMarkingToApply(e.target.value)}
                      className="flex-1 px-1.5 py-1 rounded border border-border bg-bg text-[11px]"
                    >
                      <option value="">选择标记…</option>
                      {markings.map((m) => (
                        <option key={m.id} value={m.id}>{m.name}</option>
                      ))}
                    </select>
                    <button onClick={handleApplyMarking} disabled={!markingToApply} className="btn btn-xs disabled:opacity-50">
                      打标
                    </button>
                  </div>
                </div>

                <div className="border-t border-border pt-2 text-[10px] text-text-muted">
                  资源标识：OBJECT_TYPE / <code>{otId}</code>
                </div>
              </div>
            )}

            {/* ── 定义 tab（现有建模内容）── */}
            {tab === 'definition' && (
              <>
            {/* Description */}
            {detail.description && (
              <p className="mb-4 text-xs text-text-secondary">{detail.description}</p>
            )}

            {/* Meta */}
            <div className="mb-4 grid grid-cols-2 gap-1 text-xs">
              <span className="text-text-muted">主键</span>
              <span className="font-mono text-text">{detail.primary_key}</span>
              <span className="text-text-muted">标题字段</span>
              <span className="font-mono text-text">{detail.title_property}</span>
            </div>

            {/* F4: Dataset binding section */}
            <Section title="数据集">
              {boundDataset ? (
                <div className="flex flex-col gap-1.5">
                  <div className="flex items-center justify-between">
                    <div className="text-xs">
                      <span className="font-mono text-text">{boundDataset.api_name}</span>
                      <span className="ml-1.5 text-text-muted">
                        {boundDataset.kind === 'VIRTUAL' ? '虚拟表' : '托管表'}
                        {boundDataset.data_source_api_name
                          ? ` · ${boundDataset.data_source_api_name}`
                          : ''}
                      </span>
                    </div>
                    {isVirtual && (
                      <span className="rounded-pill bg-[var(--accent-bg)] px-1.5 py-px text-[10px] text-text-muted">
                        🔒 只读
                      </span>
                    )}
                  </div>
                  {mappedProps.length > 0 && (
                    <div className="rounded-sm bg-white/[0.02] p-2 text-[11px]">
                      {mappedProps.slice(0, 5).map((p) => (
                        <div key={p.api_name} className="flex justify-between py-0.5">
                          <span className="font-mono text-text-muted">
                            {p.backing_mapping?.backing_column}
                          </span>
                          <span className="text-text-muted">→</span>
                          <span className="font-mono text-text">{p.api_name}</span>
                        </div>
                      ))}
                      {mappedProps.length > 5 && (
                        <div className="mt-0.5 text-text-muted">
                          …共 {mappedProps.length} 列映射
                        </div>
                      )}
                    </div>
                  )}
                  <button className="btn btn-xs" onClick={() => setShowLinkDialog(true)}>
                    管理关联
                  </button>
                </div>
              ) : (
                <div className="flex flex-col gap-2">
                  <span className="text-xs text-warning">⚠ 未关联数据集</span>
                  {!isVirtual && (
                    <button className="btn btn-xs" onClick={() => setShowLinkDialog(true)}>
                      关联数据集
                    </button>
                  )}
                </div>
              )}
            </Section>

            {/* Properties */}
            <Section title={`属性 (${detail.properties.length})`}>
              {detail.properties.length === 0 ? (
                <Empty />
              ) : (
                <div className="property-list flex flex-col gap-1">
                  {detail.properties.map((p) => (
                    <div
                      key={p.api_name}
                      className="property-row flex items-center justify-between rounded-sm px-2 py-1 text-sm hover:bg-white/[0.03]"
                    >
                      <span className="flex items-center gap-1.5">
                        {p.is_primary_key && (
                          <span className="rounded-pill bg-[var(--accent-bg-strong)] px-1.5 py-px text-[9px] font-bold text-accent-text">
                            PK
                          </span>
                        )}
                        {p.is_title_property && (
                          <span
                            className="rounded-pill bg-[var(--accent-bg)] px-1.5 py-px text-[9px] font-bold text-text-muted"
                            title="标题属性 (Title)"
                          >
                            T
                          </span>
                        )}
                        <span className="font-mono text-[11px] text-text">{p.api_name}</span>
                      </span>
                      <span className="flex items-center gap-1.5">
                        {p.indexed && (
                          <span title="已建索引" className="text-[10px] text-text-muted">
                            🔍
                          </span>
                        )}
                        <span className="rounded-pill bg-[var(--accent-bg)] px-1.5 py-px text-[10px] text-text-muted">
                          {p.data_type}
                        </span>
                      </span>
                    </div>
                  ))}
                </div>
              )}
            </Section>

            {/* Relationships */}
            <Section
              title={`关系 (${outgoingLinks.length + incomingLinks.length})`}
              action={
                <button
                  className="btn btn-xs"
                  onClick={() => setShowCreateLink(true)}
                  title="添加关系"
                >
                  + 添加关系
                </button>
              }
            >
              {outgoingLinks.length === 0 && incomingLinks.length === 0 ? (
                <Empty />
              ) : (
                <div className="flex flex-col gap-1.5">
                  {outgoingLinks.map((l) => (
                    <div
                      key={l.id}
                      className="relationship-row flex items-center gap-2 py-1 text-sm text-text-secondary"
                    >
                      <span className="text-[10px] font-semibold text-teal">
                        {l.cardinality === 'MANY' ? 'N:1' : '1:1'}
                      </span>
                      <span>{l.display_name}</span>
                      <span className="text-text-muted">→</span>
                      <code className="font-mono text-[10px] text-text-muted">
                        {l.target_object_type_id}
                      </code>
                    </div>
                  ))}
                  {incomingLinks.map((l) => (
                    <div
                      key={l.id}
                      className="relationship-row flex items-center gap-2 py-1 text-sm text-text-secondary"
                    >
                      <span className="text-[10px] font-semibold text-teal">被引用</span>
                      <code className="font-mono text-[10px] text-text-muted">
                        {l.source_object_type_id}
                      </code>
                      <span className="text-text-muted">→</span>
                      <span>{l.display_name}</span>
                    </div>
                  ))}
                </div>
              )}
            </Section>

            {/* Actions */}
            <Section
              title={`动作 (${objectActions.length})`}
              action={
                !isVirtual ? (
                  <button
                    className="btn btn-xs"
                    onClick={() => setEditAction({ mode: 'create' })}
                    title="新建动作"
                  >
                    + 新建动作
                  </button>
                ) : undefined
              }
            >
              {objectActions.length === 0 ? (
                <Empty text="无动作定义" />
              ) : (
                <div className="flex flex-col gap-1">
                  {objectActions.map((a) => (
                    <div key={a.id} className="flex items-center justify-between py-1 text-sm">
                      <div className="min-w-0">
                        <div className="text-text">{a.display_name}</div>
                        <code className="font-mono text-[10px] text-text-muted">{a.api_name}</code>
                      </div>
                      <div className="flex items-center gap-1">
                        {!isVirtual && (
                          <button
                            className="btn btn-xs btn-ghost"
                            title={`编辑 ${a.display_name}`}
                            onClick={() => setEditAction({ mode: 'edit', apiName: a.api_name })}
                          >
                            编辑
                          </button>
                        )}
                        <button
                          className="btn btn-xs btn-primary"
                          disabled={isVirtual || a.status !== 'ACTIVE'}
                          title={
                            isVirtual
                              ? '虚拟对象为只读，不支持执行'
                              : a.status !== 'ACTIVE'
                                ? '动作未启用'
                                : `执行 ${a.display_name}`
                          }
                          onClick={() => setExecAction(a)}
                        >
                          执行
                        </button>
                      </div>
                    </div>
                  ))}
                </div>
              )}
            </Section>
              </>
            )}

            {/* ── 能力 tab（图/时空索引启用开关，ADR-015 §capabilities）── */}
            {tab === 'capabilities' && detail && (
              <CapabilitiesTab
                detail={detail}
                ontologyName={ontologyName}
                onUpdated={(ot) => setDetail(ot)}
              />
            )}
          </>
        ) : null}
      </div>

      {/* Footer actions: 编辑主按钮 + 高危删除物理隔离（防误触） */}
      <div className="flex items-stretch gap-2 border-t border-border p-3">
        <PermissionGate action="object_type:edit" resourceId={otId} decisions={decisions} mode="disable">
          <button className="btn btn-sm flex-1" onClick={onEdit}>
            编辑
          </button>
        </PermissionGate>
        <span className="w-px self-stretch bg-border" aria-hidden="true" />
        <PermissionGate action="object_type:delete" resourceId={otId} decisions={decisions} mode="disable">
          <button
            className="btn btn-sm btn-danger"
            onClick={onDelete}
            aria-label={`删除对象 ${objectType.display_name}`}
            title="删除（高危，需二次确认）"
          >
            🗑 删除
          </button>
        </PermissionGate>
      </div>

      {/* F4/A1: dataset-link management dialog */}
      {detail && (
        <DatasetLinkDialog
          key={`link-${showLinkDialog}-${objectType.id}`}
          open={showLinkDialog}
          objectType={detail}
          datasets={datasets.filter(
            (d) => (d.kind || 'MANAGED') === (isVirtual ? 'VIRTUAL' : 'MANAGED'),
          )}
          ontologyName={ontologyName}
          onClose={() => setShowLinkDialog(false)}
          onSaved={() => {
            // Re-fetch the ObjectType so backing_mapping reflects the save.
            getObjectType(ontologyName, objectType.api_name)
              .then((ot) => setDetail(ot))
              .catch((err) => setError(formatError(err, '刷新对象详情失败')));
          }}
        />
      )}

      {/* 对象间关系创建（从向导移出，关系需目标对象已存在）*/}
      {detail && (
        <CreateLinkDialog
          open={showCreateLink}
          ontologyName={ontologyName}
          sourceObjectType={objectType}
          onClose={() => setShowCreateLink(false)}
          onCreated={() => setReloadKey((k) => k + 1)}
        />
      )}

      {/* P1 (ADR-011): execute an Action targeting this object type. */}
      {execAction && (
        <ExecuteActionDialog
          open
          onClose={() => setExecAction(null)}
          ontology={ontologyName}
          objectType={objectType.api_name}
          action={execAction}
          onApplied={() => {
            onActionApplied?.();
            setExecAction(null);
          }}
        />
      )}

      {/* ADR Action Mutation Mapping: define / edit an ActionType. */}
      {editAction && (
        <ActionTypeEditor
          open
          onClose={() => setEditAction(null)}
          ontology={ontologyName}
          affectedObjectType={objectType.api_name}
          mode={editAction.mode}
          actionApiName={editAction.apiName}
          existingActionApiNames={objectActions.map((a) => a.api_name)}
          onSaved={() => {
            setEditAction(null);
            setReloadKey((k) => k + 1);
          }}
        />
      )}
    </aside>
  );
}

function Section({
  title,
  action,
  children,
}: {
  title: string;
  /** Optional header action (e.g. an "add" button) rendered on the right. */
  action?: React.ReactNode;
  children: React.ReactNode;
}) {
  return (
    <div className="mb-4">
      <div className="mb-2 flex items-center justify-between">
        <h5 className="text-xs font-semibold uppercase tracking-wide text-text-secondary">
          {title}
        </h5>
        {action}
      </div>
      {children}
    </div>
  );
}

function Empty({ text = '无' }: { text?: string }) {
  return <p className="text-xs text-text-muted">{text}</p>;
}

/** ── 能力 Tab：图/时空索引启用开关（ADR-015 §capabilities） ──
 *
 * 对齐 Palantir Foundry Ontology Manager Capabilities tab。用户显式启用后，
 * 后端在 provision 时才创建 Neo4j label / PostGIS table / TimescaleDB hypertable，
 * 并在数据同步时投影。未启用 = 只有 Doris 基础索引（在线读主源，红线 #4）。
 *
 * 四道门判断（全部通过才写投影）：
 *   Gate 1: storage_type == MANAGED（VIRTUAL = 无数据可投影）
 *   Gate 2: data_type 匹配（GEOPOINT/GEOSHAPE→PostGIS, indexed→Neo4j）
 *   Gate 3: 关系存在（Neo4j only — 无 LinkType 则图投影无意义）
 *   Gate 4: 用户在此显式启用
 */
function CapabilitiesTab({
  detail,
  ontologyName,
  onUpdated,
}: {
  detail: ObjectType;
  ontologyName: string;
  onUpdated: (ot: ObjectType) => void;
}) {
  const isVirtual = detail.storage_type === 'VIRTUAL';
  const hasLinks = detail.links.length > 0;
  const hasSpatial = detail.properties.some(
    (p) => p.data_type === 'GEOPOINT' || p.data_type === 'GEOSHAPE',
  );
  const hasTimeseries = detail.properties.some(
    (p) => p.data_type === 'TIME_SERIES' || p.data_type === 'GEOTEMPORAL_SERIES',
  );

  const [saving, setSaving] = useState<'graph' | 'geotime' | null>(null);
  const [error, setError] = useState<string | null>(null);

  async function toggleCapability(key: 'graph_indexing_enabled' | 'geotime_indexing_enabled') {
    if (isVirtual) return;
    setSaving(key === 'graph_indexing_enabled' ? 'graph' : 'geotime');
    setError(null);
    const newCaps: ObjectTypeCapabilities = {
      ...detail.capabilities,
      [key]: !detail.capabilities[key],
    };
    try {
      const updated = await updateObjectTypeCapabilities(
        ontologyName,
        detail.api_name,
        newCaps,
      );
      onUpdated(updated);
    } catch (err) {
      setError(formatError(err, '更新能力失败'));
    } finally {
      setSaving(null);
    }
  }

  if (isVirtual) {
    return (
      <div className="rounded-lg border border-border bg-bg-secondary p-4">
        <p className="text-sm text-text-secondary">
          虚拟对象（VIRTUAL）的数据不在本地存储，无法启用图/时空索引。
          如需这些能力，请将数据接入为托管表（MANAGED）。
        </p>
      </div>
    );
  }

  return (
    <div className="space-y-4">
      {error && <div className="error-box">{error}</div>}

      {/* 图索引（Neo4j） */}
      <div className="rounded-lg border border-border p-4">
        <div className="flex items-start justify-between">
          <div className="flex-1">
            <h5 className="text-sm font-semibold text-text">图索引</h5>
            <p className="mt-1 text-xs text-text-secondary">
              启用后，对象和关系将投影到 Neo4j 图数据库，支持多跳关系遍历（searchAround）。
              仅同步 indexed 属性用于遍历剪枝，全量属性仍在 Doris。
            </p>
            <div className="mt-2 space-y-1">
              <GateCheck label="托管表（MANAGED）" passed={!isVirtual} />
              <GateCheck
                label="至少一个关系（LinkType）"
                passed={hasLinks}
                hint="当前无关系定义，启用后图投影将无边可遍历。请先创建关系。"
              />
              <GateCheck
                label="至少一个 indexed 属性（剪枝用）"
                passed={detail.properties.some((p) => p.indexed)}
              />
            </div>
          </div>
          <CapabilityToggle
            enabled={detail.capabilities.graph_indexing_enabled}
            saving={saving === 'graph'}
            disabled={saving !== null}
            onChange={() => toggleCapability('graph_indexing_enabled')}
          />
        </div>
      </div>

      {/* 时空索引（PostGIS / TimescaleDB） */}
      <div className="rounded-lg border border-border p-4">
        <div className="flex items-start justify-between">
          <div className="flex-1">
            <h5 className="text-sm font-semibold text-text">时空索引</h5>
            <p className="mt-1 text-xs text-text-secondary">
              启用后，空间属性（GEOPOINT/GEOSHAPE）投影到 PostGIS 支持空间过滤，
              时序属性（TIME_SERIES）通过 Kafka 流式写入 TimescaleDB 支持时序查询。
            </p>
            <div className="mt-2 space-y-1">
              <GateCheck label="托管表（MANAGED）" passed={!isVirtual} />
              <GateCheck
                label="空间属性（GEOPOINT/GEOSHAPE）"
                passed={hasSpatial}
                hint={hasSpatial ? undefined : '当前无空间属性，PostGIS 空间过滤将不可用。'}
              />
              <GateCheck
                label="时序属性（TIME_SERIES/GEOTEMPORAL_SERIES）"
                passed={hasTimeseries}
                hint={
                  hasTimeseries
                    ? undefined
                    : '当前无时序属性。时序查询需先定义时序属性并配置 Kafka 流式同步。'
                }
              />
            </div>
          </div>
          <CapabilityToggle
            enabled={detail.capabilities.geotime_indexing_enabled}
            saving={saving === 'geotime'}
            disabled={saving !== null}
            onChange={() => toggleCapability('geotime_indexing_enabled')}
          />
        </div>
      </div>
    </div>
  );
}

function GateCheck({
  label,
  passed,
  hint,
}: {
  label: string;
  passed: boolean;
  hint?: string;
}) {
  return (
    <div className="flex items-center gap-2 text-xs">
      <span className={passed ? 'text-green-500' : 'text-text-muted'}>
        {passed ? '✓' : '○'}
      </span>
      <span className={passed ? 'text-text' : 'text-text-muted'}>{label}</span>
      {hint && <span className="text-text-muted">— {hint}</span>}
    </div>
  );
}

function CapabilityToggle({
  enabled,
  saving,
  disabled,
  onChange,
}: {
  enabled: boolean;
  saving: boolean;
  disabled: boolean;
  onChange: () => void;
}) {
  return (
    <button
      type="button"
      role="switch"
      aria-checked={enabled}
      disabled={disabled}
      onClick={onChange}
      className={cn(
        'relative inline-flex h-6 w-11 shrink-0 cursor-pointer rounded-full border-2 border-transparent transition-colors',
        enabled ? 'bg-accent' : 'bg-border',
        disabled && 'cursor-not-allowed opacity-50',
      )}
    >
      <span
        className={cn(
          'pointer-events-none inline-block h-5 w-5 transform rounded-full bg-white shadow ring-0 transition',
          enabled ? 'translate-x-5' : 'translate-x-0',
        )}
      />
      {saving && (
        <span className="absolute -right-8 top-0.5 text-xs text-text-muted">...</span>
      )}
    </button>
  );
}
