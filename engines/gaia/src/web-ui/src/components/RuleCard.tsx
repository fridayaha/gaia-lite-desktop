/**
 * 单条规则卡（ADR Action Mutation Mapping）。
 *
 * 按 rule.type 切换字段：
 *  - Modify/Upsert/Delete：目标参数（对象引用参数下拉）+ 缺失策略
 *  - Create：目标对象类型下拉
 *  - Create/DeleteLink：关联类型 + 源/目标参数
 *  - 属性映射区：PropertyMappingRow 列表（Modify/Create/Upsert）
 *
 * 双模：编辑模式（受控）+ 只读模式（对象详情/总览预览，机制 C 复用）。
 */
import { useState } from 'react';
import { PropertyMappingRow, mappingsFromRule, mappingsToRule } from './PropertyMappingRow';
import { RULE_TYPE_LABELS } from '../lib/actionDraft';
import { cn } from '../lib/cn';
import { TextInput } from './ui/TextField';
import { Select, SelectOption } from './ui/Select';
import type {
  ActionParameterDef,
  LinkTypeDef,
  OntologyRule,
  PropertyDef,
  ValueSource,
} from '../types';

export interface RuleCardProps {
  rule: OntologyRule;
  index: number;
  total: number;
  onChange: (rule: OntologyRule) => void;
  onRemove: () => void;
  onMove: (dir: -1 | 1) => void;
  /** 目标对象类型（当前 action 归属对象）的属性——用于属性映射下拉。 */
  targetObjectProps: PropertyDef[];
  /** 本 ontology 的所有对象类型 api_name（Create 规则的目标对象类型下拉）。 */
  objectTypeApiNames: string[];
  /** 本 ontology 的所有关联类型（Link 规则的下拉）。 */
  linkTypes: LinkTypeDef[];
  /** 已定义参数（含对象引用参数，供目标参数/源参数下拉）。 */
  parameters: ActionParameterDef[];
  onParametersChange: (params: ActionParameterDef[]) => void;
  /** 是否存在对象引用参数（属性映射的 OBJECT_PROPERTY 来源用）。 */
  hasObjectRefParam: boolean;
  /** 只读模式（预览用）。 */
  readOnly?: boolean;
  /** P1: 拖拽排序手柄。传入时卡片可拖拽，头部显示手柄。 */
  draggable?: boolean;
  onDragStart?: () => void;
  onDragEnter?: () => void;
  onDragEnd?: () => void;
  isDraggedOver?: boolean;
}

const ON_MISSING_LABELS: Record<NonNullable<OntologyRule['on_missing']>, string> = {
  raise_not_found: '对象不存在时报错 (404)',
  create: '对象不存在时创建',
};

export function RuleCard({
  rule,
  index,
  total,
  onChange,
  onRemove,
  onMove,
  targetObjectProps,
  objectTypeApiNames,
  linkTypes,
  parameters,
  onParametersChange,
  hasObjectRefParam,
  readOnly = false,
  draggable = false,
  onDragStart,
  onDragEnter,
  onDragEnd,
  isDraggedOver = false,
}: RuleCardProps) {
  // 对象引用参数（作为目标参数/源参数候选）
  const objRefParams = parameters.filter((p) => p.object_type_ref);
  const isObjectRule = ['ModifyObject', 'UpsertObject', 'DeleteObject'].includes(rule.type);
  const isCreateRule = rule.type === 'CreateObject';
  const isLinkRule = rule.type === 'CreateLink' || rule.type === 'DeleteLink';
  const hasProperties = ['ModifyObject', 'UpsertObject', 'CreateObject'].includes(rule.type);

  // 本地维护属性映射行（含临时空 propName 行）。rule.properties 在 commit 时
  // 同步写入（供保存），但渲染以本地 state 为准——避免 mappingsToRule 过滤空
  // propName 后回流重置本地行（见 mappingsToRule 的 __empty__ 占位约定）。
  const [mappings, setMappings] = useState(() => mappingsFromRule(rule.properties));

  function commitMappings(next: typeof mappings) {
    setMappings(next);
    onChange({ ...rule, properties: mappingsToRule(next) });
  }

  function addMapping() {
    commitMappings([...mappings, { propName: '', source: { source: 'PARAMETER', value: '' } }]);
  }

  function updateMapping(i: number, m: (typeof mappings)[number]) {
    commitMappings(mappings.map((x, j) => (j === i ? m : x)));
  }

  function removeMapping(i: number) {
    commitMappings(mappings.filter((_, j) => j !== i));
  }

  // ── 只读模式 ──────────────────────────────────────────────
  if (readOnly) {
    return (
      <div className="card p-3">
        <div className="mb-1 flex items-center gap-2">
          <span className="rounded-pill bg-[var(--accent-bg)] px-2 py-0.5 text-[10px] font-semibold text-accent-text">
            {RULE_TYPE_LABELS[rule.type]}
          </span>
          {rule.condition && (
            <span className="font-mono text-[10px] text-text-muted">if {rule.condition}</span>
          )}
        </div>
        {isObjectRule && rule.target_parameter && (
          <div className="text-[11px] text-text-secondary">
            目标参数：<code className="font-mono">{rule.target_parameter}</code>
          </div>
        )}
        {isCreateRule && rule.target_object_type && (
          <div className="text-[11px] text-text-secondary">
            目标类型：<code className="font-mono">{rule.target_object_type}</code>
          </div>
        )}
        {hasProperties && mappings.length > 0 && (
          <div className="mt-1 flex flex-col gap-0.5">
            {mappings.map((m, i) => (
              <div key={i} className="font-mono text-[11px] text-text-secondary">
                {m.propName} ← {m.source.source}
                {m.source.value ? `(${m.source.value})` : ''}
              </div>
            ))}
          </div>
        )}
        {isLinkRule && (
          <div className="text-[11px] text-text-secondary">
            关联：<code>{rule.link_type}</code> · {rule.source_parameter} →{' '}
            {rule.target_link_parameter}
          </div>
        )}
      </div>
    );
  }

  // ── 编辑模式 ──────────────────────────────────────────────
  return (
    <div
      className={cn('card p-3', isDraggedOver && 'ring-2 ring-accent')}
      draggable={draggable}
      onDragStart={draggable ? onDragStart : undefined}
      onDragEnter={draggable ? onDragEnter : undefined}
      onDragEnd={draggable ? onDragEnd : undefined}
      onDragOver={draggable ? (e) => e.preventDefault() : undefined}
    >
      {/* 头部：拖拽手柄 + 类型 + 删除 */}
      <div className="mb-2 flex items-center justify-between">
        <div className="flex items-center gap-1.5">
          {draggable && (
            <span
              className="cursor-grab text-text-muted active:cursor-grabbing"
              title="拖拽排序"
              aria-label="拖拽排序"
            >
              ⠿
            </span>
          )}
          <span className="rounded-pill bg-[var(--accent-bg)] px-2 py-0.5 text-[11px] font-semibold text-accent-text">
            {RULE_TYPE_LABELS[rule.type]}
          </span>
        </div>
        <div className="flex items-center gap-1">
          {/* P1: 保留上下移动作为键盘可达的 fallback */}
          <button
            className="btn btn-xs btn-ghost px-1.5"
            onClick={() => onMove(-1)}
            disabled={index === 0}
            aria-label="上移"
            title="上移"
          >
            ▲
          </button>
          <button
            className="btn btn-xs btn-ghost px-1.5"
            onClick={() => onMove(1)}
            disabled={index === total - 1}
            aria-label="下移"
            title="下移"
          >
            ▼
          </button>
          <button
            className="btn btn-xs btn-danger-outline px-1.5"
            onClick={onRemove}
            aria-label="删除规则"
            title="删除规则"
          >
            ✕
          </button>
        </div>
      </div>

      {/* 目标定位区 */}
      {isObjectRule && (
        <div className="mb-2 grid grid-cols-2 gap-2">
          <label className="flex flex-col gap-1 text-xs">
            <span className="text-text-secondary">目标参数（对象引用）</span>
            <Select
              inputClassName="form-select text-xs"
              value={rule.target_parameter ?? ''}
              onChange={(v) => onChange({ ...rule, target_parameter: v || null })}
              placeholder="— 选择 —"
              aria-label="目标参数"
            >
              <SelectOption value="" label="— 选择 —" />
              {objRefParams.map((p) => (
                <SelectOption
                  key={p.api_name}
                  value={p.api_name}
                  label={`${p.api_name} (${p.object_type_ref})`}
                />
              ))}
            </Select>
          </label>
          {(rule.type === 'ModifyObject' || rule.type === 'UpsertObject') && (
            <label className="flex flex-col gap-1 text-xs">
              <span className="text-text-secondary">对象缺失策略</span>
              <Select
                inputClassName="form-select text-xs"
                value={rule.on_missing ?? 'raise_not_found'}
                onChange={(v) =>
                  onChange({
                    ...rule,
                    on_missing: v as OntologyRule['on_missing'],
                  })
                }
                aria-label="对象缺失策略"
              >
                <SelectOption value="raise_not_found" label={ON_MISSING_LABELS.raise_not_found} />
                <SelectOption value="create" label={ON_MISSING_LABELS.create} />
              </Select>
            </label>
          )}
        </div>
      )}

      {isCreateRule && (
        <div className="mb-2">
          <label className="flex flex-col gap-1 text-xs">
            <span className="text-text-secondary">目标对象类型</span>
            <Select
              inputClassName="form-select text-xs"
              value={rule.target_object_type ?? ''}
              onChange={(v) => onChange({ ...rule, target_object_type: v || null })}
              placeholder="— 选择 —"
              aria-label="目标对象类型"
            >
              <SelectOption value="" label="— 选择 —" />
              {objectTypeApiNames.map((n) => (
                <SelectOption key={n} value={n} label={n} />
              ))}
            </Select>
          </label>
        </div>
      )}

      {isLinkRule && (
        <div className="mb-2">
          <div className="mb-1.5 rounded-sm border border-border bg-[var(--bg)] px-2 py-1 text-[11px] text-text-muted">
            💡
            多对多关联用本规则建立/删除链接；一对多/一对一关联（外键）请用「修改对象」规则改外键属性。
          </div>
          <div className="grid grid-cols-3 gap-2">
            <label className="flex flex-col gap-1 text-xs">
              <span className="text-text-secondary">关联类型</span>
              <Select
                inputClassName="form-select text-xs"
                value={rule.link_type ?? ''}
                onChange={(v) => onChange({ ...rule, link_type: v || null })}
                placeholder="— 选择 —"
                aria-label="关联类型"
              >
                <SelectOption value="" label="— 选择 —" />
                {linkTypes.map((l) => (
                  <SelectOption
                    key={l.api_name}
                    value={l.api_name}
                    label={`${l.display_name || l.api_name} (${l.api_name})`}
                  />
                ))}
              </Select>
            </label>
            <label className="flex flex-col gap-1 text-xs">
              <span className="text-text-secondary">源参数</span>
              <Select
                inputClassName="form-select text-xs"
                value={rule.source_parameter ?? ''}
                onChange={(v) => onChange({ ...rule, source_parameter: v || null })}
                placeholder="— 选择 —"
                aria-label="源参数"
              >
                <SelectOption value="" label="— 选择 —" />
                {objRefParams.map((p) => (
                  <SelectOption key={p.api_name} value={p.api_name} label={p.api_name} />
                ))}
              </Select>
            </label>
            <label className="flex flex-col gap-1 text-xs">
              <span className="text-text-secondary">目标参数</span>
              <Select
                inputClassName="form-select text-xs"
                value={rule.target_link_parameter ?? ''}
                onChange={(v) => onChange({ ...rule, target_link_parameter: v || null })}
                placeholder="— 选择 —"
                aria-label="目标参数"
              >
                <SelectOption value="" label="— 选择 —" />
                {objRefParams.map((p) => (
                  <SelectOption key={p.api_name} value={p.api_name} label={p.api_name} />
                ))}
              </Select>
            </label>
          </div>
          {rule.link_type && (
            <div className="mt-1 text-[11px] text-text-muted">
              {(() => {
                const lt = linkTypes.find((l) => l.api_name === rule.link_type);
                if (!lt) return null;
                return (
                  <span>
                    关联类型：{lt.cardinality === 'ONE' ? '一对一/一对多' : '多对多'}
                    {lt.foreign_key_property_api_name
                      ? ` · 外键属性 ${lt.foreign_key_property_api_name}（建议用修改对象规则改外键）`
                      : ''}
                  </span>
                );
              })()}
            </div>
          )}
        </div>
      )}

      {/* 属性映射区 */}
      {hasProperties && (
        <div className="mb-2">
          <div className="mb-1 text-[11px] font-semibold uppercase tracking-wide text-text-muted">
            属性映射
          </div>
          <div className="flex flex-col gap-1.5">
            {mappings.map((m, i) => (
              <PropertyMappingRow
                key={i}
                mapping={m}
                onChange={(mm) => updateMapping(i, mm)}
                onRemove={() => removeMapping(i)}
                objectProps={targetObjectProps}
                ruleType={rule.type as 'CreateObject' | 'ModifyObject' | 'UpsertObject'}
                parameters={parameters}
                onParametersChange={onParametersChange}
                hasObjectRefParam={hasObjectRefParam}
              />
            ))}
            {mappings.length === 0 && (
              <p className="text-[11px] text-text-muted">暂无属性映射，点击下方添加。</p>
            )}
          </div>
          <button className="btn btn-xs btn-ghost mt-1.5" onClick={addMapping}>
            + 添加属性
          </button>
        </div>
      )}

      {/* 条件（可选） */}
      <label className="flex flex-col gap-1 text-xs">
        <span className="text-text-secondary">条件（可选，满足时才执行）</span>
        <TextInput
          inputClassName={cn('form-input font-mono text-xs')}
          value={rule.condition ?? ''}
          onChange={(v) => onChange({ ...rule, condition: v || null })}
          placeholder="如 delay_minutes > 0"
        />
      </label>
    </div>
  );
}

/** 把 OntologyRule.properties 的 ValueSource 重新组装回 OntologyRule（保存时用）。 */
// eslint-disable-next-line react-refresh/only-export-components
export function ruleWithProperties(
  rule: OntologyRule,
  props: Record<string, ValueSource>,
): OntologyRule {
  return { ...rule, properties: props };
}
