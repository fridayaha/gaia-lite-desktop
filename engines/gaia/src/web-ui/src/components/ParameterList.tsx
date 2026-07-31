/**
 * 参数列表（ADR Action Mutation Mapping · 机制 A 副产物）。
 *
 * 参数由规则属性映射自动派生，此处可微调。自动派生参数（ⓘ）不可直接删除
 * —— 需先移除规则引用。受控组件。
 */
import { isParameterAutoDerived } from '../lib/actionDraft';
import { cn } from '../lib/cn';
import { TextInput } from './ui/TextField';
import { Select, SelectOption } from './ui/Select';
import type { ActionParameterDef, DataType, OntologyRule } from '../types';

export interface ParameterListProps {
  parameters: ActionParameterDef[];
  onChange: (params: ActionParameterDef[]) => void;
  ontologyRules: OntologyRule[];
  /** 本 ontology 的对象类型 api_name（对象引用类型下拉）。 */
  objectTypeApiNames: string[];
  readOnly?: boolean;
}

const DATA_TYPES: DataType[] = [
  'STRING',
  'INTEGER',
  'LONG',
  'SHORT',
  'FLOAT',
  'DOUBLE',
  'DECIMAL',
  'BOOLEAN',
  'DATE',
  'TIMESTAMP',
];

export function ParameterList({
  parameters,
  onChange,
  ontologyRules,
  objectTypeApiNames,
  readOnly = false,
}: ParameterListProps) {
  function update(i: number, patch: Partial<ActionParameterDef>) {
    onChange(parameters.map((p, j) => (j === i ? { ...p, ...patch } : p)));
  }
  function remove(i: number) {
    onChange(parameters.filter((_, j) => j !== i));
  }
  function add() {
    onChange([
      ...parameters,
      {
        api_name: '',
        display_name: '',
        data_type: 'STRING',
        required: true,
        object_type_ref: null,
      },
    ]);
  }

  if (readOnly) {
    if (parameters.length === 0) return <p className="text-xs text-text-muted">无参数</p>;
    return (
      <div className="flex flex-col gap-1">
        {parameters.map((p) => (
          <div key={p.api_name} className="font-mono text-[11px] text-text-secondary">
            {p.api_name} : {p.data_type}
            {p.object_type_ref ? ` (对象引用 ${p.object_type_ref})` : ''}
            {p.required ? ' · 必填' : ''}
          </div>
        ))}
      </div>
    );
  }

  return (
    <div className="flex flex-col gap-2">
      {parameters.length === 0 && (
        <p className="text-xs text-text-muted">
          无参数。添加规则属性映射时会自动生成同名参数；也可手动添加。
        </p>
      )}
      {parameters.map((p, i) => {
        const auto = isParameterAutoDerived(p.api_name, ontologyRules);
        return (
          <div key={i} className="card p-2.5">
            <div className="mb-1.5 flex items-center justify-between">
              <div className="flex items-center gap-1.5">
                <span className="font-mono text-xs font-semibold">{p.api_name || '(未命名)'}</span>
                <span className="rounded-pill bg-[var(--surface)] px-1.5 py-px text-[10px] text-text-muted">
                  {p.data_type}
                  {p.object_type_ref ? ` · 对象引用` : ''}
                </span>
                {auto && (
                  <span className="text-[10px] text-text-muted" title="由规则自动生成">
                    ⓘ 自动
                  </span>
                )}
              </div>
              <button
                className="btn btn-xs btn-danger-outline px-1.5"
                onClick={() => remove(i)}
                disabled={auto}
                title={auto ? '由规则自动生成，请先移除规则中的引用' : '删除参数'}
                aria-label="删除参数"
              >
                ✕
              </button>
            </div>
            <div className="grid grid-cols-2 gap-2">
              <label className="flex flex-col gap-1 text-[11px]">
                <span className="text-text-muted">API 名称</span>
                <TextInput
                  inputClassName="form-input font-mono text-xs"
                  value={p.api_name}
                  onChange={(v) => update(i, { api_name: v })}
                  placeholder="delayMinutes"
                />
              </label>
              <label className="flex flex-col gap-1 text-[11px]">
                <span className="text-text-muted">显示名称</span>
                <TextInput
                  inputClassName="form-input text-xs"
                  value={p.display_name ?? ''}
                  onChange={(v) => update(i, { display_name: v })}
                />
              </label>
              <label className="flex flex-col gap-1 text-[11px]">
                <span className="text-text-muted">数据类型</span>
                <Select
                  inputClassName="form-select text-xs"
                  value={p.data_type}
                  onChange={(v) => update(i, { data_type: v as DataType })}
                  aria-label="数据类型"
                >
                  {DATA_TYPES.map((t) => (
                    <SelectOption key={t} value={t} label={t} />
                  ))}
                </Select>
              </label>
              <label className="flex flex-col gap-1 text-[11px]">
                <span className="text-text-muted">对象引用类型（可选）</span>
                <Select
                  inputClassName="form-select text-xs"
                  value={p.object_type_ref ?? ''}
                  onChange={(v) => update(i, { object_type_ref: v || null })}
                  placeholder="（普通参数）"
                  aria-label="对象引用类型"
                >
                  <SelectOption value="" label="（普通参数）" />
                  {objectTypeApiNames.map((n) => (
                    <SelectOption key={n} value={n} label={n} />
                  ))}
                </Select>
              </label>
            </div>
            <div className="mt-1.5 flex flex-wrap items-center gap-3 text-[11px]">
              <label className="flex items-center gap-1">
                <input
                  type="checkbox"
                  className="h-3.5 w-3.5"
                  checked={p.required !== false}
                  onChange={(e) => update(i, { required: e.target.checked })}
                />
                <span className="text-text-secondary">必填</span>
              </label>
              <label className="flex items-center gap-1">
                <input
                  type="checkbox"
                  className="h-3.5 w-3.5"
                  checked={p.readonly ?? false}
                  onChange={(e) => update(i, { readonly: e.target.checked })}
                />
                <span className="text-text-secondary">只读</span>
              </label>
              <label className="flex items-center gap-1">
                <input
                  type="checkbox"
                  className="h-3.5 w-3.5"
                  checked={p.hidden ?? false}
                  onChange={(e) => update(i, { hidden: e.target.checked })}
                />
                <span className="text-text-secondary">隐藏</span>
              </label>
              <label className="flex items-center gap-1">
                <input
                  type="checkbox"
                  className="h-3.5 w-3.5"
                  checked={p.is_object_set ?? false}
                  onChange={(e) => update(i, { is_object_set: e.target.checked })}
                />
                <span className="text-text-secondary">对象集合</span>
              </label>
              <label className={cn('flex items-center gap-1')}>
                <span className="text-text-secondary">默认值</span>
                <TextInput
                  inputClassName="form-input h-6 w-24 text-xs"
                  value={p.default != null ? String(p.default) : ''}
                  onChange={(v) => update(i, { default: v })}
                  placeholder="（无）"
                />
              </label>
            </div>
          </div>
        );
      })}
      <button className="btn btn-xs btn-ghost self-start" onClick={add}>
        + 添加参数
      </button>
    </div>
  );
}
