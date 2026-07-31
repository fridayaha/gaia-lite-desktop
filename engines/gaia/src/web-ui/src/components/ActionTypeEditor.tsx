/**
 * ActionType 编辑器（ADR Action Mutation Mapping · 主组件）。
 *
 * 对标 Palantir Ontology Manager 的 Action 编辑面板。结构：
 *  ① 基本信息（显示名/描述/目标对象锁定/风险/操作类型/批量）
 *  ② 规则（统一「+ 添加规则」下拉含 Ontology Rules + 副作用，机制 C）
 *  ③ 参数（机制 A：自动派生 + 微调）
 *  ④ 校验规则
 *  ⑤ 预览（干跑，不落库）
 *
 * 提交：create → defineActionType；edit → updateActionType（发布新版本）。
 */
import { useEffect, useMemo, useState } from 'react';
import { Modal } from './Modal';
import { RuleCard } from './RuleCard';
import { ParameterList } from './ParameterList';
import { EffectConfigForm } from './EffectConfigForm';
import { ActionPreviewPanel, makePreviewAction } from './ActionPreviewPanel';
import { VersionHistoryInline } from './VersionHistoryInline';
import { useAsyncAction } from '../hooks/useAsyncAction';
import { formatError } from '../lib/formatError';
import {
  defineActionType,
  getActionType,
  getObjectType,
  listObjectTypeSummaries,
  listLinkTypes,
  updateActionType,
} from '../api/client';
import { scaffoldActionType } from '../api/ai';
import {
  draftFromRecord,
  draftToPayload,
  draftToUpdatePayload,
  emptyDraft,
  EFFECT_TYPE_LABELS,
  newCreateRule,
  newDeleteRule,
  newLinkRule,
  newModifyRule,
  newUpsertRule,
  validateDraft,
  type ActionDraft,
  type ValidationError,
} from '../lib/actionDraft';
import { cn } from '../lib/cn';
import { TextInput } from './ui/TextField';
import { Select, SelectOption } from './ui/Select';
import type {
  ActionEffectConfig,
  ActionParameterDef,
  ActionRule,
  LinkTypeDef,
  ObjectType,
  ObjectTypeSummary,
  OntologyRule,
  ValueSource,
} from '../types';

export interface ActionTypeEditorProps {
  open: boolean;
  onClose: () => void;
  ontology: string;
  /** 目标对象类型 api_name（action 归属，新建时锁定）。 */
  affectedObjectType: string;
  mode: 'create' | 'edit';
  /** edit 模式：要编辑的 action api_name。 */
  actionApiName?: string;
  /** 已存在的 action api_name 列表（create 模式查重用）。 */
  existingActionApiNames: string[];
  onSaved: () => void;
}

const RISK_LABELS: Record<ActionDraft['risk_level'], string> = {
  low: '低',
  medium: '中',
  high: '高',
};

const OP_KIND_LABELS: Record<ActionDraft['operation_kind'], string> = {
  create: '创建',
  update: '更新',
  delete: '删除',
  mixed: '混合',
};

export function ActionTypeEditor({
  open,
  onClose,
  ontology,
  affectedObjectType,
  mode,
  actionApiName,
  existingActionApiNames,
  onSaved,
}: ActionTypeEditorProps) {
  const { loading, error, run } = useAsyncAction();
  const [draft, setDraft] = useState<ActionDraft>(() => emptyDraft(affectedObjectType));
  const [errors, setErrors] = useState<ValidationError[]>([]);
  const [showPreview, setShowPreview] = useState(false);
  const [version, setVersion] = useState<number | null>(null);
  // P1: 规则拖拽排序状态
  const [dragIndex, setDragIndex] = useState<number | null>(null);
  const [overIndex, setOverIndex] = useState<number | null>(null);
  // P1: 版本历史内联区
  const [showVersions, setShowVersions] = useState(false);
  const [reloadKey, setReloadKey] = useState(0);
  // AI 生成动作草稿（/ai/action-type/scaffold）：输入框 + 流式状态
  const [aiInput, setAiInput] = useState('');
  const [aiLoading, setAiLoading] = useState(false);
  const [aiError, setAiError] = useState<string | null>(null);
  const [aiPending, setAiPending] = useState<string[]>([]);

  // 编辑器所需的上下文数据
  const [targetObjectType, setTargetObjectType] = useState<ObjectType | null>(null);
  const [objectTypeApiNames, setObjectTypeApiNames] = useState<string[]>([]);
  const [linkTypes, setLinkTypes] = useState<LinkTypeDef[]>([]);
  const [loadError, setLoadError] = useState<string | null>(null);

  // ── 加载上下文 + 编辑回填 ──────────────────────────────────
  useEffect(() => {
    if (!open) return;
    let cancelled = false;
    (async () => {
      setLoadError(null);
      try {
        const [ot, summaries, links] = await Promise.all([
          getObjectType(ontology, affectedObjectType),
          listObjectTypeSummaries(ontology).catch(() => [] as ObjectTypeSummary[]),
          listLinkTypes(ontology).catch(() => [] as LinkTypeDef[]),
        ]);
        if (cancelled) return;
        setTargetObjectType(ot);
        setObjectTypeApiNames(summaries.map((s) => s.api_name));
        setLinkTypes(links);

        if (mode === 'edit' && actionApiName) {
          const a = await getActionType(ontology, actionApiName);
          if (cancelled) return;
          const d = draftFromRecord(a);
          d.affected_object_type_api_name = affectedObjectType;
          setDraft(d);
          setVersion(a.version ?? null);
        } else {
          setDraft(emptyDraft(affectedObjectType));
          setVersion(null);
        }
      } catch (e) {
        if (!cancelled) setLoadError(formatError(e));
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [open, ontology, affectedObjectType, mode, actionApiName, reloadKey]);

  const targetProps = targetObjectType?.properties ?? [];
  const hasObjectRefParam = draft.parameters.some((p) => p.object_type_ref);

  // ── 草稿更新辅助 ──────────────────────────────────────────
  function patchDraft(p: Partial<ActionDraft>) {
    setDraft((d) => ({ ...d, ...p }));
  }

  /** AI 生成动作草稿：调 /ai/action-type/scaffold 流式填充表单。
   *
   * 后端给 LLM 注入真实 ObjectType schema（防幻觉），流式返回草稿 partial。
   * 每个 partial 直接覆盖当前 draft（渐进式填充）。流结束后若有校验错误，
   * 后端会在最后一帧的 pending_confirmations 里标注，此处展示给用户。
   * 草稿不落库——用户微调后点「保存」走正常 defineActionType。 */
  async function runAiScaffold() {
    const desc = aiInput.trim();
    if (!desc) return;
    setAiLoading(true);
    setAiError(null);
    setAiPending([]);
    try {
      for await (const frame of scaffoldActionType({
        ontology,
        affected_object_type: affectedObjectType,
        natural_language: desc,
      })) {
        if ('error' in frame) {
          setAiError(frame.error);
          break;
        }
        // 流式覆盖：把后端 draft 字段映射到编辑器 ActionDraft。
        // submission_criteria 当前 ActionDraft 不直接展示（校验规则区用 rules），
        // 仍保留在 draft 供保存时序列化。这里映射核心字段。
        setDraft((d) => ({
          ...d,
          api_name: frame.api_name ?? d.api_name,
          display_name: frame.display_name ?? d.display_name,
          description: frame.description ?? d.description,
          parameters: (frame.parameters ?? []).map((p) => ({
            api_name: p.api_name,
            display_name: p.display_name,
            data_type: p.data_type,
            required: p.required,
            default: p.default,
            description: p.description,
            default_source: p.default_source as ActionParameterDef['default_source'],
            default_source_field: p.default_source_field,
            readonly: p.readonly,
            hidden: p.hidden,
            pattern: p.pattern,
            error_message: p.error_message,
            enum_values: p.enum_values,
            object_type_ref: p.object_type_ref,
            is_object_set: p.is_object_set,
          })),
          rules: (frame.rules ?? []).map((r) => ({
            type: r.type as ActionRule['type'],
            target: r.target,
            expression: r.expression,
            description: r.description,
          })),
          ontology_rules: (frame.ontology_rules ?? []).map((r) => ({
            type: r.type as OntologyRule['type'],
            target_parameter: r.target_parameter,
            target_object_type: r.target_object_type,
            target_path: null,
            properties: Object.fromEntries(
              Object.entries(r.properties).map(([k, v]) => [
                k,
                { source: v.source as ValueSource['source'], value: v.value },
              ]),
            ),
            link_type: r.link_type,
            source_parameter: r.source_parameter,
            target_link_parameter: r.target_link_parameter,
            condition: r.condition,
            on_missing: r.on_missing as OntologyRule['on_missing'],
            description: r.description,
          })),
          effects: (frame.effects ?? []).map((e) => ({
            type: e.type as ActionEffectConfig['type'],
            config: e.config,
            trigger: e.trigger as ActionEffectConfig['trigger'],
            condition: e.condition,
          })),
          risk_level: (frame.risk_level ?? d.risk_level) as ActionDraft['risk_level'],
          operation_kind: (frame.operation_kind ?? d.operation_kind) as ActionDraft['operation_kind'],
          batch_enabled: frame.batch_enabled ?? d.batch_enabled,
        }));
        if (frame.pending_confirmations && frame.pending_confirmations.length > 0) {
          setAiPending(frame.pending_confirmations);
        }
      }
    } catch (e) {
      setAiError(formatError(e));
    } finally {
      setAiLoading(false);
    }
  }
  function updateRule(i: number, rule: OntologyRule) {
    setDraft((d) => ({
      ...d,
      ontology_rules: d.ontology_rules.map((r, j) => (j === i ? rule : r)),
    }));
  }
  function removeRule(i: number) {
    setDraft((d) => ({ ...d, ontology_rules: d.ontology_rules.filter((_, j) => j !== i) }));
  }
  function moveRule(i: number, dir: -1 | 1) {
    setDraft((d) => {
      const next = [...d.ontology_rules];
      const j = i + dir;
      if (j < 0 || j >= next.length) return d;
      [next[i], next[j]] = [next[j], next[i]];
      return { ...d, ontology_rules: next };
    });
  }
  /** P1: 拖拽落位 —— 把 dragIndex 的规则移到 overIndex 位置。 */
  function dropRule() {
    if (dragIndex == null || overIndex == null || dragIndex === overIndex) {
      setDragIndex(null);
      setOverIndex(null);
      return;
    }
    setDraft((d) => {
      const next = [...d.ontology_rules];
      const [moved] = next.splice(dragIndex, 1);
      next.splice(overIndex, 0, moved);
      return { ...d, ontology_rules: next };
    });
    setDragIndex(null);
    setOverIndex(null);
  }
  function addRule(type: OntologyRule['type']) {
    let rule: OntologyRule;
    switch (type) {
      case 'ModifyObject':
        rule = newModifyRule();
        break;
      case 'CreateObject':
        rule = newCreateRule(affectedObjectType);
        break;
      case 'UpsertObject':
        rule = newUpsertRule();
        break;
      case 'DeleteObject':
        rule = newDeleteRule();
        break;
      case 'CreateLink':
      case 'DeleteLink':
        rule = newLinkRule(type);
        break;
    }
    setDraft((d) => ({ ...d, ontology_rules: [...d.ontology_rules, rule] }));
  }
  function addEffect(type: ActionEffectConfig['type']) {
    const effect: ActionEffectConfig = {
      type,
      config: {},
      trigger: 'AFTER_ONTOLOGY_CHANGE',
      condition: null,
    };
    setDraft((d) => ({ ...d, effects: [...d.effects, effect] }));
  }
  function updateEffect(i: number, effect: ActionEffectConfig) {
    setDraft((d) => ({
      ...d,
      effects: d.effects.map((e, j) => (j === i ? effect : e)),
    }));
  }
  function removeEffect(i: number) {
    setDraft((d) => ({ ...d, effects: d.effects.filter((_, j) => j !== i) }));
  }

  // ── 保存 ──────────────────────────────────────────────────
  async function handleSave() {
    const errs = validateDraft(draft, targetProps, existingActionApiNames, mode === 'create');
    setErrors(errs);
    if (errs.length > 0) return;

    const result = await run(async () => {
      if (mode === 'create') {
        return defineActionType(ontology, draft.api_name, draftToPayload(draft));
      }
      return updateActionType(ontology, draft.api_name, draftToUpdatePayload(draft));
    });
    if (result) {
      onSaved();
    }
  }

  const previewAction = useMemo(
    () => makePreviewAction(draft.api_name, draft.display_name, draft.parameters),
    [draft.api_name, draft.display_name, draft.parameters],
  );

  const ruleMenuItems: {
    type: OntologyRule['type'] | ActionEffectConfig['type'];
    label: string;
    isEffect: boolean;
  }[] = [
    { type: 'ModifyObject', label: '修改对象', isEffect: false },
    { type: 'CreateObject', label: '创建对象', isEffect: false },
    { type: 'UpsertObject', label: '创建或修改对象', isEffect: false },
    { type: 'DeleteObject', label: '删除对象', isEffect: false },
    { type: 'CreateLink', label: '创建关联', isEffect: false },
    { type: 'DeleteLink', label: '删除关联', isEffect: false },
    { type: 'notification', label: '通知', isEffect: true },
    { type: 'webhook', label: 'Webhook', isEffect: true },
    { type: 'write_back', label: '回写源表', isEffect: true },
  ];

  return (
    <Modal
      open={open}
      onClose={onClose}
      ariaLabel={mode === 'create' ? '新建动作' : `编辑动作 ${actionApiName}`}
      overlayClassName="dialog-overlay"
      panelClassName="dialog p-0 overflow-hidden max-w-none max-h-[90vh] flex flex-col"
      style={{ width: '760px', maxWidth: '95vw' }}
    >
      <div className="flex max-h-[90vh] min-h-0 flex-col overflow-hidden p-5">
        {/* 标题栏 */}
        <div className="mb-3 flex items-center justify-between border-b border-border pb-2">
          <h2 className="text-base font-semibold">
            {mode === 'create' ? '新建动作' : '编辑动作'}
            {version != null && (
              <span className="ml-2 text-xs font-normal text-text-muted">v{version}</span>
            )}
            {mode === 'edit' && actionApiName && (
              <button
                className="ml-2 text-[11px] font-normal text-accent-text hover:underline"
                onClick={() => setShowVersions((s) => !s)}
              >
                {showVersions ? '收起历史' : '历史'}
              </button>
            )}
          </h2>
        </div>

        {/* AI 生成区：输入自然语言 → 流式填充草稿 */}
        <div className="mb-3 rounded-sm border border-accent-border bg-accent-bg-subtle px-3 py-2">
          <div className="flex items-center gap-2">
            <input
              className="input flex-1"
              placeholder="描述动作意图，如：当工单状态为 Open 时，把优先级改成 P0/P1/P2，并通知负责人"
              value={aiInput}
              onChange={(e) => setAiInput(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === 'Enter' && !e.shiftKey) {
                  e.preventDefault();
                  runAiScaffold();
                }
              }}
              disabled={aiLoading}
            />
            <button
              className="btn btn-xs btn-primary"
              onClick={runAiScaffold}
              disabled={aiLoading || !aiInput.trim()}
            >
              {aiLoading ? '生成中…' : '生成'}
            </button>
          </div>
          {aiError && (
            <div className="mt-1.5 text-xs text-error-text">{aiError}</div>
          )}
          {aiPending.length > 0 && (
            <div className="mt-1.5 text-xs text-text-muted">
              {aiPending.map((p, i) => (
                <div key={i}>{p}</div>
              ))}
            </div>
          )}
          <div className="mt-1 text-[11px] text-text-muted">
            AI 生成的是草稿，请检查后点「保存」。只会引用「{affectedObjectType}」的真实属性。
          </div>
        </div>

        {/* P1: 版本历史内联区 */}
        {showVersions && mode === 'edit' && actionApiName && (
          <VersionHistoryInline
            ontology={ontology}
            actionApiName={actionApiName}
            currentVersion={version}
            onRolledBack={() => {
              setShowVersions(false);
              // 触发重新加载草稿
              setReloadKey((k) => k + 1);
            }}
          />
        )}

        {loadError && (
          <div className="mb-2 rounded-sm border border-error-border bg-error-bg px-2 py-1 text-xs text-error-text">
            {loadError}
          </div>
        )}
        {errors.length > 0 && (
          <div className="mb-2 rounded-sm border border-error-border bg-error-bg px-2 py-1.5 text-xs text-error-text">
            <div className="font-medium">请修正以下问题：</div>
            <ul className="mt-0.5 list-disc pl-4">
              {errors.map((e, i) => (
                <li key={i}>{e.message}</li>
              ))}
            </ul>
          </div>
        )}

        {/* 滚动内容区 */}
        <div className="flex-1 min-h-0 overflow-y-auto">
          {/* ① 基本信息 */}
          <Section title="基本信息">
            <div className="grid grid-cols-2 gap-2">
              <Field label="显示名称" required>
                <TextInput
                  inputClassName="form-input text-sm"
                  value={draft.display_name}
                  onChange={(v) => patchDraft({ display_name: v })}
                  placeholder="航班延误处理"
                />
              </Field>
              <Field label="API 名称" required>
                <TextInput
                  inputClassName="form-input font-mono text-sm"
                  value={draft.api_name}
                  onChange={(v) => patchDraft({ api_name: v })}
                  placeholder="delayFlight"
                  disabled={mode === 'edit'}
                  title={mode === 'edit' ? '编辑态不可修改 API 名称' : undefined}
                />
              </Field>
            </div>
            <Field label="描述">
              <TextInput
                inputClassName="form-input text-sm"
                value={draft.description}
                onChange={(v) => patchDraft({ description: v })}
                placeholder="对动作的简短描述"
              />
            </Field>
            <div className="grid grid-cols-2 gap-2">
              <Field label="目标对象类型">
                <TextInput
                  inputClassName="form-input bg-[var(--bg)] text-sm text-text-muted"
                  value={affectedObjectType}
                  disabled
                  title="动作归属此对象类型，不可更改"
                />
              </Field>
              <Field label="操作类型">
                <Select
                  inputClassName="form-select text-sm"
                  value={draft.operation_kind}
                  onChange={(v) =>
                    patchDraft({
                      operation_kind: v as ActionDraft['operation_kind'],
                    })
                  }
                  aria-label="操作类型"
                >
                  {Object.entries(OP_KIND_LABELS).map(([k, v]) => (
                    <SelectOption key={k} value={k} label={v} />
                  ))}
                </Select>
              </Field>
            </div>
            <div className="flex flex-wrap items-center gap-4">
              <div className="flex items-center gap-2">
                <span className="text-xs text-text-secondary">风险等级</span>
                {(['low', 'medium', 'high'] as const).map((r) => (
                  <label key={r} className="flex items-center gap-1 text-xs">
                    <input
                      type="radio"
                      name="risk-level"
                      className="h-3.5 w-3.5"
                      checked={draft.risk_level === r}
                      onChange={() => patchDraft({ risk_level: r })}
                    />
                    <span className="text-text-secondary">{RISK_LABELS[r]}</span>
                  </label>
                ))}
              </div>
              <label className="flex items-center gap-1 text-xs">
                <input
                  type="checkbox"
                  className="h-3.5 w-3.5"
                  checked={draft.batch_enabled}
                  onChange={(e) => patchDraft({ batch_enabled: e.target.checked })}
                />
                <span className="text-text-secondary">启用批量执行</span>
              </label>
            </div>
          </Section>

          {/* ② 规则 */}
          <Section title="规则">
            <div className="flex flex-col gap-2">
              {draft.ontology_rules.map((rule, i) => (
                <RuleCard
                  key={i}
                  rule={rule}
                  index={i}
                  total={draft.ontology_rules.length}
                  onChange={(r) => updateRule(i, r)}
                  onRemove={() => removeRule(i)}
                  onMove={(dir) => moveRule(i, dir)}
                  targetObjectProps={targetProps}
                  objectTypeApiNames={objectTypeApiNames}
                  linkTypes={linkTypes}
                  parameters={draft.parameters}
                  onParametersChange={(params: ActionParameterDef[]) =>
                    patchDraft({ parameters: params })
                  }
                  hasObjectRefParam={hasObjectRefParam}
                  draggable
                  onDragStart={() => setDragIndex(i)}
                  onDragEnter={() => setOverIndex(i)}
                  onDragEnd={dropRule}
                  isDraggedOver={overIndex === i && dragIndex !== i}
                />
              ))}
              {draft.effects.map((effect, i) => (
                <div key={`eff-${i}`} className="card p-3">
                  <div className="mb-2 flex items-center justify-between">
                    <span className="rounded-pill bg-[var(--surface)] px-2 py-0.5 text-[11px] font-semibold text-text-secondary">
                      {EFFECT_TYPE_LABELS[effect.type]}
                    </span>
                    <button
                      className="btn btn-xs btn-danger-outline px-1.5"
                      onClick={() => removeEffect(i)}
                      aria-label="删除副作用"
                    >
                      ✕
                    </button>
                  </div>
                  <EffectConfigForm
                    effect={effect}
                    onChange={(e) => updateEffect(i, e)}
                    objectTypeApiNames={objectTypeApiNames}
                  />
                </div>
              ))}
              {draft.ontology_rules.length === 0 && draft.effects.length === 0 && (
                <p className="text-xs text-text-muted">
                  无规则。添加第一条规则开始定义动作会做什么。
                </p>
              )}
            </div>
            <AddRuleMenu onAddRule={addRule} onAddEffect={addEffect} items={ruleMenuItems} />
            <p className="mt-1 text-[11px] text-text-muted">
              ⓘ 规则按顺序执行；同一对象只能有一个操作（修改/创建/删除互斥）。
            </p>
          </Section>

          {/* ③ 参数 */}
          <Section title="参数">
            <ParameterList
              parameters={draft.parameters}
              onChange={(params) => patchDraft({ parameters: params })}
              ontologyRules={draft.ontology_rules}
              objectTypeApiNames={objectTypeApiNames}
            />
          </Section>

          {/* ④ 校验规则 */}
          <Section title="校验规则">
            <ValidationRuleList
              rules={draft.rules}
              parameters={draft.parameters}
              onChange={(rules: ActionRule[]) => patchDraft({ rules })}
            />
          </Section>

          {/* ⑤ 预览 */}
          <Section title="预览">
            <button className="btn btn-xs btn-ghost" onClick={() => setShowPreview((s) => !s)}>
              {showPreview ? '收起预览' : '展开预览（干跑）'}
            </button>
            {showPreview && (
              <div className="mt-2">
                {mode === 'edit' && actionApiName ? (
                  <ActionPreviewPanel
                    ontology={ontology}
                    objectType={affectedObjectType}
                    action={previewAction}
                  />
                ) : (
                  <p className="text-[11px] text-text-muted">
                    保存动作后即可在此干跑预览（编辑态可用）。
                  </p>
                )}
              </div>
            )}
          </Section>
        </div>

        {/* 底部操作栏 */}
        <div className="mt-3 flex items-center justify-end gap-2 border-t border-border pt-2">
          {error && <span className="mr-auto text-xs text-error-text">{formatError(error)}</span>}
          <button className="btn btn-ghost" onClick={onClose} disabled={loading}>
            取消
          </button>
          <button
            className={cn('btn btn-primary', loading && 'is-loading')}
            onClick={handleSave}
            disabled={loading}
          >
            {loading ? '保存中…' : '保存'}
          </button>
        </div>
      </div>
    </Modal>
  );
}

// ── 子组件 ──────────────────────────────────────────────────

function Section({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <div className="mb-4">
      <h4 className="mb-2 text-xs font-semibold uppercase tracking-wide text-text-secondary">
        {title}
      </h4>
      {children}
    </div>
  );
}

function Field({
  label,
  required,
  children,
}: {
  label: string;
  required?: boolean;
  children: React.ReactNode;
}) {
  return (
    <label className="mb-2 flex flex-col gap-1 text-xs">
      <span className="text-text-secondary">
        {label}
        {required && <span className="text-error"> *</span>}
      </span>
      {children}
    </label>
  );
}

function AddRuleMenu({
  onAddRule,
  onAddEffect,
  items,
}: {
  onAddRule: (t: OntologyRule['type']) => void;
  onAddEffect: (t: ActionEffectConfig['type']) => void;
  items: {
    type: OntologyRule['type'] | ActionEffectConfig['type'];
    label: string;
    isEffect: boolean;
  }[];
}) {
  const [open, setOpen] = useState(false);
  return (
    <div className="relative mt-2">
      <button className="btn btn-xs btn-primary" onClick={() => setOpen((s) => !s)}>
        + 添加规则 ▾
      </button>
      {open && (
        <>
          <div className="fixed inset-0 z-10" onClick={() => setOpen(false)} />
          <div className="absolute left-0 top-full z-20 mt-1 w-44 rounded-md border border-border bg-surface py-1 shadow-lg">
            <div className="px-2 py-0.5 text-[10px] uppercase text-text-muted">变更对象</div>
            {items
              .filter((i) => !i.isEffect)
              .map((i) => (
                <button
                  key={i.type}
                  className="block w-full px-3 py-1 text-left text-xs hover:bg-[var(--accent-bg)]"
                  onClick={() => {
                    onAddRule(i.type as OntologyRule['type']);
                    setOpen(false);
                  }}
                >
                  {i.label}
                </button>
              ))}
            <div className="mt-1 border-t border-border px-2 py-0.5 text-[10px] uppercase text-text-muted">
              副作用
            </div>
            {items
              .filter((i) => i.isEffect)
              .map((i) => (
                <button
                  key={i.type}
                  className="block w-full px-3 py-1 text-left text-xs hover:bg-[var(--accent-bg)]"
                  onClick={() => {
                    onAddEffect(i.type as ActionEffectConfig['type']);
                    setOpen(false);
                  }}
                >
                  {i.label}
                </button>
              ))}
          </div>
        </>
      )}
    </div>
  );
}

function ValidationRuleList({
  rules,
  parameters,
  onChange,
}: {
  rules: ActionRule[];
  parameters: ActionParameterDef[];
  onChange: (rules: ActionRule[]) => void;
}) {
  function add() {
    onChange([...rules, { type: 'validation', target: '', expression: '', description: '' }]);
  }
  function update(i: number, patch: Partial<ActionRule>) {
    onChange(rules.map((r, j) => (j === i ? { ...r, ...patch } : r)));
  }
  function remove(i: number) {
    onChange(rules.filter((_, j) => j !== i));
  }
  return (
    <div className="flex flex-col gap-1.5">
      {rules.length === 0 && (
        <p className="text-xs text-text-muted">无校验规则。可添加如「delay_minutes &gt; 0」。</p>
      )}
      {rules.map((r, i) => (
        <div key={i} className="flex items-center gap-1.5">
          <Select
            inputClassName="form-select w-[140px] text-xs"
            value={r.target}
            onChange={(v) => update(i, { target: v })}
            placeholder="— 参数 —"
            aria-label="目标参数"
          >
            <SelectOption value="" label="— 参数 —" />
            {parameters.map((p) => (
              <SelectOption key={p.api_name} value={p.api_name} label={p.api_name} />
            ))}
          </Select>
          <TextInput
            inputClassName="form-input flex-1 font-mono text-xs"
            value={r.expression}
            onChange={(v) => update(i, { expression: v })}
            placeholder="delay_minutes > 0"
            aria-label="表达式"
          />
          <button
            className="btn btn-xs btn-danger-outline px-1.5"
            onClick={() => remove(i)}
            aria-label="删除校验"
          >
            ✕
          </button>
        </div>
      ))}
      <button className="btn btn-xs btn-ghost self-start" onClick={add}>
        + 添加校验
      </button>
    </div>
  );
}
