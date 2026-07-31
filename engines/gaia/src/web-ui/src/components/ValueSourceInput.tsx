/**
 * 值来源组合控件（ADR Action Mutation Mapping · 机制 B）。
 *
 * 一行属性映射的「来源 + 值」组合：来源下拉按属性类型动态收窄（见
 * availableSources），值控件随来源变形。受控组件。
 *
 * Palantir 对齐：属性映射行的值来源是配置式 Action 的核心交互单元。
 */
import { cn } from '../lib/cn';
import { availableSources, systemContextOptions } from '../lib/actionDraft';
import { TextInput } from './ui/TextField';
import { Select, SelectOption } from './ui/Select';
import type { ActionParameterDef, ValueSource } from '../types';

export interface ValueSourceInputProps {
  value: ValueSource;
  onChange: (v: ValueSource) => void;
  /** 目标属性数据类型（决定可用来源）。 */
  propType: string;
  /** 目标属性是否为主键（主键可 SYSTEM_GENERATED，且不可出现在 Modify）。 */
  isPrimaryKey: boolean;
  /** 已定义的参数列表（PARAMETER 来源的下拉项）。 */
  parameters: ActionParameterDef[];
  /** 是否存在对象引用参数（决定 OBJECT_PROPERTY 是否可选）。 */
  hasObjectRefParam: boolean;
}

const SOURCE_LABELS: Record<ValueSource['source'], string> = {
  PARAMETER: '参数',
  STATIC_VALUE: '静态值',
  SYSTEM_CONTEXT: '系统',
  SYSTEM_GENERATED: '系统生成',
  OBJECT_PROPERTY: '对象属性',
  EXPRESSION: '表达式',
};

export function ValueSourceInput({
  value,
  onChange,
  propType,
  isPrimaryKey,
  parameters,
  hasObjectRefParam,
}: ValueSourceInputProps) {
  const sources = availableSources(propType, isPrimaryKey, hasObjectRefParam);
  // 当前来源若被收窄掉，回退到第一个可用来源
  const currentSource: ValueSource['source'] = sources.includes(value.source)
    ? value.source
    : sources[0];

  function update(patch: Partial<ValueSource>) {
    onChange({ ...value, source: currentSource, ...patch });
  }

  return (
    <div className="flex flex-1 items-center gap-1.5">
      <Select
        inputClassName="form-select w-[120px] text-xs"
        value={currentSource}
        onChange={(v) => {
          const src = v as ValueSource['source'];
          // 切换来源时重置 value，避免残留无效值
          onChange({ source: src, value: defaultValueForSource(src) });
        }}
        aria-label="值来源"
      >
        {sources.map((s) => (
          <SelectOption key={s} value={s} label={SOURCE_LABELS[s]} />
        ))}
      </Select>

      <ValueControl
        source={currentSource}
        value={value.value ?? ''}
        propType={propType}
        parameters={parameters}
        onChange={(v) => update({ value: v })}
      />
    </div>
  );
}

function defaultValueForSource(src: ValueSource['source']): string | null {
  switch (src) {
    case 'SYSTEM_GENERATED':
      return 'uuid';
    case 'SYSTEM_CONTEXT':
      return null; // 由 ValueControl 选第一个
    default:
      return '';
  }
}

function ValueControl({
  source,
  value,
  propType,
  parameters,
  onChange,
}: {
  source: ValueSource['source'];
  value: string;
  propType: string;
  parameters: ActionParameterDef[];
  onChange: (v: string) => void;
}) {
  const t = propType.toUpperCase();

  if (source === 'PARAMETER') {
    return (
      <Select
        inputClassName="form-select flex-1 text-xs"
        value={value}
        onChange={onChange}
        placeholder="— 选择参数 —"
        aria-label="选择参数"
      >
        <SelectOption value="" label="— 选择参数 —" />
        {parameters.map((p) => (
          <SelectOption
            key={p.api_name}
            value={p.api_name}
            label={`${p.display_name || p.api_name} (${p.api_name})`}
          />
        ))}
      </Select>
    );
  }

  if (source === 'SYSTEM_CONTEXT') {
    const opts = systemContextOptions(propType);
    const current = opts.includes(value) ? value : (opts[0] ?? '');
    return (
      <Select
        inputClassName="form-select flex-1 text-xs"
        value={current}
        onChange={onChange}
        aria-label="系统上下文"
      >
        {opts.map((o) => (
          <SelectOption
            key={o}
            value={o}
            label={o === 'CURRENT_USER_ID' ? '当前用户 ID' : '当前时间'}
          />
        ))}
      </Select>
    );
  }

  if (source === 'SYSTEM_GENERATED') {
    return (
      <Select
        inputClassName="form-select flex-1 text-xs"
        value={value || 'uuid'}
        onChange={onChange}
        aria-label="系统生成"
      >
        <SelectOption value="uuid" label="UUID" />
      </Select>
    );
  }

  if (source === 'OBJECT_PROPERTY') {
    // 决策 7：仅单层「参数名.属性名」。先选对象引用参数。
    const objRefParams = parameters.filter((p) => p.object_type_ref);
    const [paramPart, propPart] = value.split('.');
    return (
      <div className="flex flex-1 items-center gap-1">
        <Select
          inputClassName="form-select text-xs"
          value={paramPart ?? ''}
          onChange={(v) => onChange(propPart ? `${v}.${propPart}` : v)}
          placeholder="— 对象参数 —"
          aria-label="对象参数"
        >
          <SelectOption value="" label="— 对象参数 —" />
          {objRefParams.map((p) => (
            <SelectOption key={p.api_name} value={p.api_name} label={p.api_name} />
          ))}
        </Select>
        <span className="text-text-muted">.</span>
        <TextInput
          inputClassName="form-input flex-1 text-xs"
          value={propPart ?? ''}
          onChange={(v) => onChange(paramPart ? `${paramPart}.${v}` : v)}
          placeholder="属性名"
          aria-label="对象属性名"
        />
      </div>
    );
  }

  if (source === 'EXPRESSION') {
    return (
      <TextInput
        inputClassName="form-input flex-1 font-mono text-xs"
        value={value}
        onChange={onChange}
        placeholder="如 delay_minutes + 1"
        aria-label="表达式"
      />
    );
  }

  // STATIC_VALUE：按属性类型选控件
  if (t === 'BOOLEAN') {
    return (
      <Select
        inputClassName="form-select flex-1 text-xs"
        value={value}
        onChange={onChange}
        aria-label="布尔值"
      >
        <SelectOption value="true" label="true" />
        <SelectOption value="false" label="false" />
      </Select>
    );
  }
  const isNumeric = ['INTEGER', 'LONG', 'SHORT', 'FLOAT', 'DOUBLE', 'DECIMAL'].includes(t);
  return (
    <TextInput
      type={isNumeric ? 'number' : 'text'}
      inputClassName={cn('form-input flex-1 text-xs', !isNumeric && 'text-xs')}
      value={value}
      onChange={onChange}
      placeholder={isNumeric ? '0' : '静态值'}
      aria-label="静态值"
    />
  );
}
