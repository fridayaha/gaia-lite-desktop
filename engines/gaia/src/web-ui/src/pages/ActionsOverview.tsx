import { useState, useEffect, useMemo } from 'react';
import { listOntologies, listActionTypes, listObjectTypeSummaries } from '../api/client';
import { useAllowedActions } from '../hooks/useAllowedActions';
import { PermissionGate } from '../components/permission';

const ACTION_EXECUTE = 'action_type:execute';
import { cn } from '../lib/cn';
import { SkeletonList } from '../components/Skeleton';
import { ExecuteActionDialog } from '../components/ExecuteActionDialog';
import { RuleCard } from '../components/RuleCard';
import { ParameterList } from '../components/ParameterList';
import { EFFECT_TYPE_LABELS } from '../lib/actionDraft';
import type {
  ActionTypeRecord,
  ActionEffectConfig,
  ActionParameterDef,
  ObjectTypeSummary,
  OntologyRule,
} from '../types';

interface ActionWithOntology extends ActionTypeRecord {
  ontologyApiName: string;
  ontologyDisplayName: string;
}

export function ActionsOverview() {
  const [actions, setActions] = useState<ActionWithOntology[]>([]);
  const [virtualOtIds, setVirtualOtIds] = useState<Set<string>>(new Set());
  const [otIdToApiName, setOtIdToApiName] = useState<Record<string, string>>({});
  const [loading, setLoading] = useState(true);
  const [execTarget, setExecTarget] = useState<ActionWithOntology | null>(null);
  const [objectTypeInput, setObjectTypeInput] = useState('');
  const [detailAction, setDetailAction] = useState<ActionWithOntology | null>(null);

  // Ship-the-decision：批量获取动作执行权限（设计 §8.2）。
  const actionIds = useMemo(() => actions.map((a) => a.api_name), [actions]);
  const { decisions } = useAllowedActions('ACTION_TYPE', actionIds);

  useEffect(() => {
    let cancelled = false;
    async function loadActions() {
      setLoading(true);
      try {
        const ontos = await listOntologies();
        if (cancelled) return;
        const all: ActionWithOntology[] = [];
        const virtualIds = new Set<string>();
        const idMap: Record<string, string> = {};
        for (const onto of ontos) {
          // F5: load object type summaries so we can flag actions whose
          // affected object type is VIRTUAL (write guard — VIRTUAL objects
          // are read-only external proxies; Actions must target MANAGED).
          try {
            const summaries: ObjectTypeSummary[] = await listObjectTypeSummaries(onto.api_name);
            for (const s of summaries) {
              if (s.storage_type === 'VIRTUAL') virtualIds.add(s.id);
              idMap[s.id] = s.api_name; // P1 (ADR-011): resolve ot id → api_name
            }
          } catch {
            /* summaries optional — degrade to no read-only markers */
          }
          try {
            const acts = await listActionTypes(onto.api_name);
            for (const a of acts) {
              all.push({
                ...a,
                ontologyApiName: onto.api_name,
                ontologyDisplayName: onto.display_name,
              });
            }
          } catch {
            /* skip */
          }
        }
        if (cancelled) return;
        setActions(all);
        setVirtualOtIds(virtualIds);
        setOtIdToApiName(idMap);
      } finally {
        if (!cancelled) setLoading(false);
      }
    }
    loadActions();
    return () => {
      cancelled = true;
    };
  }, []);

  const statusBadge = (s: string) => (
    <span
      className={cn('object-card-status', s === 'ACTIVE' ? 'status-active' : 'status-experimental')}
    >
      {s === 'ACTIVE' ? '已启用' : s}
    </span>
  );

  return (
    <div>
      <div className="page-header">
        <h1>能力赋予</h1>
      </div>
      <p className="mb-4 text-sm text-text-secondary">
        AI 可执行的操作列表 — 操作定义归属 ① 业务定义，此处为全局总览
      </p>

      {loading ? (
        <div className="card-grid">
          {Array.from({ length: 4 }).map((_, i) => (
            <div key={i} className="card">
              <SkeletonList rows={3} />
            </div>
          ))}
        </div>
      ) : actions.length === 0 ? (
        <div className="empty-state">
          <h2>还没有定义操作</h2>
          <p>进入 ① 业务定义 → 选择对象 → 在详情中定义操作</p>
        </div>
      ) : (
        <div className="card-grid">
          {actions.map((a) => {
            const isVirtualTarget =
              a.affected_object_type_id != null && virtualOtIds.has(a.affected_object_type_id);
            return (
              <div
                key={a.id}
                className="card cursor-pointer transition hover:border-accent"
                onClick={() => setDetailAction(a)}
                role="button"
                tabIndex={0}
                onKeyDown={(e) => {
                  if (e.key === 'Enter' || e.key === ' ') {
                    e.preventDefault();
                    setDetailAction(a);
                  }
                }}
              >
                <div className="object-card-header">
                  <span className="object-card-name">{a.display_name}</span>
                  {isVirtualTarget ? (
                    <span
                      className="object-card-status status-experimental"
                      title="目标对象为虚拟对象，不可写入"
                    >
                      🔒 只读
                    </span>
                  ) : (
                    statusBadge(a.status)
                  )}
                </div>
                <div className="mb-2 text-[11px] text-text-muted">
                  {a.ontologyDisplayName} · {a.api_name}
                </div>
                <div className="text-xs text-text-secondary">{a.description || '暂无描述'}</div>
                <div className="mt-3 flex gap-2">
                  <PermissionGate
                    action={ACTION_EXECUTE}
                    resourceId={a.api_name}
                    decisions={decisions}
                    mode="disable"
                  >
                    <button
                      className="btn btn-sm btn-primary"
                      disabled={isVirtualTarget}
                      title={isVirtualTarget ? '虚拟对象为只读，不支持执行写入操作' : '执行操作'}
                      onClick={(e) => {
                        e.stopPropagation();
                        // P1 (ADR-011): resolve object_type api_name from the
                        // affected_object_type_id via the summaries map; only
                        // fall back to manual input when the summary is missing.
                        const resolved =
                          a.affected_object_type_id != null
                            ? otIdToApiName[a.affected_object_type_id]
                            : undefined;
                        setExecTarget(a);
                        setObjectTypeInput(resolved ?? '');
                    }}
                  >
                    执行
                  </button>
                  </PermissionGate>
                </div>
              </div>
            );
          })}
        </div>
      )}

      {execTarget && (
        <>
          {/* Action 总览页不知道受影响 object_type 的 api_name，需用户填 */}
          {!objectTypeInput ? (
            <div
              className="fixed inset-0 z-50 flex items-center justify-center bg-black/50"
              onClick={(e) => e.target === e.currentTarget && setExecTarget(null)}
            >
              <div className="card max-w-sm p-4">
                <h2 className="mb-2 text-sm font-semibold">输入目标对象类型 api_name</h2>
                <input
                  className="input mb-3"
                  placeholder="如 order"
                  value={objectTypeInput}
                  onChange={(e) => setObjectTypeInput(e.target.value)}
                  autoFocus
                />
                <div className="flex justify-end gap-2">
                  <button className="btn btn-ghost" onClick={() => setExecTarget(null)}>
                    取消
                  </button>
                  <button
                    className="btn btn-primary"
                    disabled={!objectTypeInput.trim()}
                    onClick={() => setObjectTypeInput(objectTypeInput.trim())}
                  >
                    下一步
                  </button>
                </div>
              </div>
            </div>
          ) : (
            <ExecuteActionDialog
              open
              onClose={() => {
                setExecTarget(null);
                setObjectTypeInput('');
              }}
              ontology={execTarget.ontologyApiName}
              objectType={objectTypeInput}
              action={execTarget}
            />
          )}
        </>
      )}

      {/* P1: 只读详情抽屉 */}
      {detailAction && (
        <ActionReadonlyDrawer
          action={detailAction}
          onClose={() => setDetailAction(null)}
          objectTypeApiName={
            detailAction.affected_object_type_id != null
              ? (otIdToApiName[detailAction.affected_object_type_id] ?? '')
              : ''
          }
        />
      )}
    </div>
  );
}

/** 只读详情抽屉：展示参数/规则/副作用。 */
function ActionReadonlyDrawer({
  action,
  onClose,
  objectTypeApiName,
}: {
  action: ActionWithOntology;
  onClose: () => void;
  objectTypeApiName: string;
}) {
  const params = (action.parameters ?? {}) as Record<string, unknown>;
  const paramDefs = (params.parameters as ActionParameterDef[] | undefined) ?? [];
  const ontologyRules = (params.ontology_rules as OntologyRule[] | undefined) ?? [];
  const effects = (params.effects as ActionEffectConfig[] | undefined) ?? [];
  const rules =
    ((action.rules as Record<string, unknown> | undefined)?.rules as
      { type: string; target: string; expression: string }[] | undefined) ?? [];

  return (
    <div
      className="fixed inset-0 z-50 flex justify-end bg-black/50"
      onMouseDown={(e) => e.target === e.currentTarget && onClose()}
      role="presentation"
    >
      <aside
        className="flex h-full w-[420px] max-w-[95vw] flex-col border-l border-border bg-sidebar"
        role="dialog"
        aria-modal="true"
        aria-label={`动作详情 ${action.display_name}`}
        style={{ animation: 'drawer-in 0.2s ease' }}
      >
        <div className="flex items-start justify-between border-b border-border px-4 py-3">
          <div className="min-w-0">
            <div className="flex items-center gap-2">
              <span className="truncate text-sm font-semibold text-text">
                {action.display_name}
              </span>
              <span
                className={cn(
                  'object-card-status',
                  action.status === 'ACTIVE' ? 'status-active' : 'status-experimental',
                )}
              >
                {action.status === 'ACTIVE' ? '已启用' : action.status}
              </span>
            </div>
            <code className="font-mono text-[11px] text-text-muted">{action.api_name}</code>
            <div className="text-[11px] text-text-muted">
              {action.ontologyDisplayName} · 目标对象 {objectTypeApiName || '—'} · v
              {action.version ?? 1}
            </div>
          </div>
          <button className="btn btn-xs" aria-label="关闭" onClick={onClose}>
            ✕
          </button>
        </div>

        <div className="flex-1 overflow-y-auto p-4">
          {action.description && (
            <p className="mb-3 text-xs text-text-secondary">{action.description}</p>
          )}

          <ReadonlySection title="参数">
            <ParameterList
              parameters={paramDefs}
              onChange={() => {}}
              ontologyRules={ontologyRules}
              objectTypeApiNames={[]}
              readOnly
            />
          </ReadonlySection>

          <ReadonlySection title={`变更规则 (${ontologyRules.length})`}>
            {ontologyRules.length === 0 ? (
              <p className="text-xs text-text-muted">无规则</p>
            ) : (
              <div className="flex flex-col gap-2">
                {ontologyRules.map((r, i) => (
                  <RuleCard
                    key={i}
                    rule={r}
                    index={i}
                    total={ontologyRules.length}
                    onChange={() => {}}
                    onRemove={() => {}}
                    onMove={() => {}}
                    targetObjectProps={[]}
                    objectTypeApiNames={[]}
                    linkTypes={[]}
                    parameters={paramDefs}
                    onParametersChange={() => {}}
                    hasObjectRefParam={paramDefs.some((p) => p.object_type_ref)}
                    readOnly
                  />
                ))}
              </div>
            )}
          </ReadonlySection>

          <ReadonlySection title={`副作用 (${effects.length})`}>
            {effects.length === 0 ? (
              <p className="text-xs text-text-muted">无副作用</p>
            ) : (
              <div className="flex flex-col gap-1">
                {effects.map((e, i) => (
                  <div key={i} className="card p-2 text-xs">
                    <span className="rounded-pill bg-[var(--surface)] px-2 py-0.5 text-[11px] font-semibold text-text-secondary">
                      {EFFECT_TYPE_LABELS[e.type]}
                    </span>
                    <span className="ml-2 text-text-muted">
                      {e.trigger === 'BEFORE_ONTOLOGY_CHANGE' ? '变更前' : '变更后'}
                    </span>
                    {e.condition && (
                      <span className="ml-2 font-mono text-[10px] text-text-muted">
                        if {e.condition}
                      </span>
                    )}
                  </div>
                ))}
              </div>
            )}
          </ReadonlySection>

          <ReadonlySection title={`校验规则 (${rules.length})`}>
            {rules.length === 0 ? (
              <p className="text-xs text-text-muted">无校验规则</p>
            ) : (
              <div className="flex flex-col gap-1">
                {rules.map((r, i) => (
                  <div key={i} className="font-mono text-[11px] text-text-secondary">
                    {r.target}: {r.expression}
                  </div>
                ))}
              </div>
            )}
          </ReadonlySection>
        </div>
      </aside>
    </div>
  );
}

function ReadonlySection({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <div className="mb-4">
      <h5 className="mb-2 text-xs font-semibold uppercase tracking-wide text-text-secondary">
        {title}
      </h5>
      {children}
    </div>
  );
}
