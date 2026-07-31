import { cn } from '../lib/cn';
import { controlKindFor } from '../lib/actionForm';
import { ObjectPicker } from './ObjectPicker';
import { TextInput } from './ui/TextField';
import { Select, SelectOption } from './ui/Select';
import type { ActionParameterDef } from '../types';

interface ActionParameterFieldProps {
  def: ActionParameterDef;
  value: string;
  onChange: (value: string) => void;
  /** 本体 api_name —— 传入时 object_type_ref 参数渲染为对象选择器（P1）。 */
  ontology?: string;
}

/**
 * Renders a single Action parameter input, choosing the control by data_type
 * and parameter metadata (P1, ADR-011).
 *
 * - BOOLEAN → checkbox
 * - DATE → date picker
 * - TIMESTAMP → datetime-local picker
 * - enum_values non-empty → <select>
 * - object_type_ref set + ontology provided → ObjectPicker (searchable)
 * - object_type_ref set + no ontology → text input (object id)
 * - otherwise → text input
 *
 * Honors `readonly` (disabled input) and `hidden` is filtered by the parent
 * before rendering.
 */
export function ActionParameterField({
  def,
  value,
  onChange,
  ontology,
}: ActionParameterFieldProps) {
  const kind = controlKindFor(def);
  const label = def.display_name || def.api_name;
  const requiredMark = def.required !== false ? ' *' : '';

  if (kind === 'checkbox') {
    const checked = value.toLowerCase() === 'true';
    return (
      <label className="flex items-center gap-2 text-sm">
        <input
          type="checkbox"
          className="h-4 w-4"
          checked={checked}
          disabled={def.readonly}
          onChange={(e) => onChange(e.target.checked ? 'true' : 'false')}
        />
        <span className="text-text-secondary">
          {label}
          {requiredMark}
          <span className="ml-1 text-[10px] text-text-muted">({def.data_type})</span>
        </span>
      </label>
    );
  }

  if (kind === 'select' && def.enum_values) {
    return (
      <label className="flex flex-col gap-1 text-sm">
        <span className="text-text-secondary">
          {label}
          {requiredMark}
          <span className="ml-1 text-[10px] text-text-muted">({def.data_type})</span>
        </span>
        <Select
          inputClassName="form-input"
          value={value}
          disabled={def.readonly}
          onChange={onChange}
          placeholder="— 选择 —"
          aria-label={label}
        >
          <SelectOption label="— 选择 —" value="" />
          {def.enum_values.map((v) => (
            <SelectOption key={v} label={v} value={v} />
          ))}
        </Select>
      </label>
    );
  }

  // object-ref with ontology context → searchable ObjectPicker (P1)
  if (kind === 'object-ref' && def.object_type_ref && ontology) {
    return (
      <label className="flex flex-col gap-1 text-sm">
        <span className="text-text-secondary">
          {label}
          {requiredMark}
          <span className="ml-1 text-[10px] text-text-muted">(对象引用 {def.object_type_ref})</span>
        </span>
        <ObjectPicker
          ontology={ontology}
          objectType={def.object_type_ref}
          value={value}
          onChange={onChange}
          disabled={def.readonly}
        />
      </label>
    );
  }

  return (
    <label className="flex flex-col gap-1 text-sm">
      <span className="text-text-secondary">
        {label}
        {requiredMark}
        {def.object_type_ref ? (
          <span className="ml-1 text-[10px] text-text-muted">({def.object_type_ref} ID)</span>
        ) : (
          <span className="ml-1 text-[10px] text-text-muted">({def.data_type})</span>
        )}
      </span>
      <TextInput
        type={kind === 'object-ref' ? 'text' : kind}
        inputClassName={cn('form-input', def.readonly && 'opacity-60')}
        value={value}
        disabled={def.readonly}
        onChange={onChange}
        placeholder={
          def.object_type_ref
            ? `输入 ${def.object_type_ref} 主键`
            : def.default != null
              ? String(def.default)
              : ''
        }
      />
    </label>
  );
}
