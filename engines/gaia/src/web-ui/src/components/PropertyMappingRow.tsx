/**
 * 属性映射行（ADR Action Mutation Mapping · 机制 A）。
 *
 * 一行 = [属性名 ▼] [ValueSourceInput] [✕]。
 *  - ModifyObject：主键属性置灰不可选（ADR §3.8 主键不可修改）
 *  - CreateObject：主键属性必选且高亮
 *  - 选择「参数」来源且目标参数不存在 → 自动派生同名参数（Palantir 行为）
 */
import { ValueSourceInput } from './ValueSourceInput';
import { ensureParameterForMapping } from '../lib/actionDraft';
import { cn } from '../lib/cn';
import { Select, SelectOption } from './ui/Select';
import type { ActionParameterDef, PropertyDef, ValueSource } from '../types';

export interface PropertyMapping {
  /** 属性 api_name（key）。空串表示尚未选择。 */
  propName: string;
  source: ValueSource;
}

export interface PropertyMappingRowProps {
  mapping: PropertyMapping;
  onChange: (mapping: PropertyMapping) => void;
  onRemove: () => void;
  /** 目标对象类型的全部属性（下拉项 + 主键判定 + 类型）。 */
  objectProps: PropertyDef[];
  /** 规则类型：决定主键是「禁用」(Modify) 还是「必选」(Create)。 */
  ruleType: 'CreateObject' | 'ModifyObject' | 'UpsertObject' | 'DeleteObject';
  /** 已定义参数（ValueSourceInput 用 + 自动派生回写）。 */
  parameters: ActionParameterDef[];
  onParametersChange: (params: ActionParameterDef[]) => void;
  /** 是否存在对象引用参数。 */
  hasObjectRefParam: boolean;
}

export function PropertyMappingRow({
  mapping,
  onChange,
  onRemove,
  objectProps,
  ruleType,
  parameters,
  onParametersChange,
  hasObjectRefParam,
}: PropertyMappingRowProps) {
  const prop = objectProps.find((p) => p.api_name === mapping.propName);
  const isPk = prop?.is_primary_key ?? false;
  // 历史/未知属性：rule.properties 的 key 不在当前 OT 属性列表里
  // （如 ActionType 定义后 OT 属性被重命名）。显示为可见 option 而非空白
  // placeholder，让用户能看出问题、主动重映射，而不是误以为「没选」。
  const isUnknownProp = Boolean(mapping.propName) && !prop;

  function selectProp(apiName: string) {
    const p = objectProps.find((x) => x.api_name === apiName);
    if (!p) {
      onChange({ ...mapping, propName: '', source: mapping.source });
      return;
    }
    onChange({ ...mapping, propName: apiName });
  }

  function handleSourceChange(source: ValueSource) {
    onChange({ ...mapping, source });
    // 机制 A：选「参数」来源时，若 value 指向的参数不存在，自动派生
    if (source.source === 'PARAMETER' && source.value) {
      const dataType = prop?.data_type ?? 'STRING';
      const next = ensureParameterForMapping(parameters, source.value, dataType, {
        object_type_ref: null,
      });
      if (next !== parameters) onParametersChange(next);
    }
  }

  return (
    <div className="flex items-center gap-1.5">
      {/* 属性名下拉 */}
      <Select
        inputClassName={cn('form-select w-[140px] text-xs', !mapping.propName && 'text-text-muted')}
        value={mapping.propName}
        onChange={selectProp}
        placeholder="— 属性 —"
        aria-label="属性名"
      >
        <SelectOption value="" label="— 属性 —" />
        {objectProps.map((p) => {
          // ModifyObject：主键不可选
          const isDisabled = ruleType === 'ModifyObject' && p.is_primary_key;
          return (
            <SelectOption
              key={p.api_name}
              value={p.api_name}
              isDisabled={isDisabled}
              label={`${p.api_name}${p.is_primary_key ? ' (主键)' : ''}`}
            />
          );
        })}
        {isUnknownProp && (
          <SelectOption
            value={mapping.propName}
            label={`${mapping.propName} (未知属性)`}
          />
        )}
      </Select>

      {/* 值来源 */}
      <div className="flex flex-1 items-center">
        {mapping.propName ? (
          <ValueSourceInput
            value={mapping.source}
            onChange={handleSourceChange}
            propType={prop?.data_type ?? 'STRING'}
            isPrimaryKey={isPk}
            parameters={parameters}
            hasObjectRefParam={hasObjectRefParam}
          />
        ) : (
          <span className="text-xs text-text-muted">请先选择属性</span>
        )}
      </div>

      <button
        className="btn btn-xs btn-danger-outline px-1.5"
        onClick={onRemove}
        aria-label="删除属性映射"
        title="删除"
      >
        ✕
      </button>
    </div>
  );
}

/** 在 OntologyRule.properties (Record<string, ValueSource>) 与行数组间转换。 */
// eslint-disable-next-line react-refresh/only-export-components
export function mappingsFromRule(
  properties: Record<string, ValueSource> | undefined,
): PropertyMapping[] {
  if (!properties) return [];
  return Object.entries(properties).map(([propName, source]) => ({
    propName: propName === '__empty__' ? '' : propName,
    source,
  }));
}

// eslint-disable-next-line react-refresh/only-export-components
export function mappingsToRule(mappings: PropertyMapping[]): Record<string, ValueSource> {
  // 保留所有行（含临时空 propName 行）以便本地 state 双向同步；
  // 空 propName 在 draftToPayload / validateDraft 层最终过滤。
  const out: Record<string, ValueSource> = {};
  for (const m of mappings) {
    out[m.propName || '__empty__'] = m.source;
  }
  return out;
}
