/**
 * Project-level Select wrapper around React Aria Components (ADR-013 Phase 4).
 *
 * Replaces native `<select className="form-select">` with a headless,
 * accessible custom listbox (React Aria `Select` + `Button` + `Popover` +
 * `ListBox`). Benefits over native `<select>`:
 *  - Consistent styling across browsers/OSes (native dropdowns vary wildly)
 *  - Full keyboard navigation + type-ahead (React Aria production-grade a11y)
 *  - Composable with other React Aria primitives (FieldError, Label)
 *
 * The wrapper keeps a native-like API: `value`/`onChange(string)` plus
 * `children` as `<option>`-shaped items via the `SelectOption` helper, so
 * call sites read almost like the native element. `inputClassName` applies
 * the `form-select` look to the trigger button.
 *
 * See ADR-013.
 */
import {
  Select as AriaSelect,
  Button,
  SelectValue,
  Popover,
  ListBox,
  ListBoxItem,
  type SelectProps,
} from 'react-aria-components';
import { cn } from '../../lib/cn';

export interface SelectOptionProps {
  /** Option label shown to the user. */
  label: string;
  /** Option value (string). When omitted, the label is used as the value. */
  value?: string;
  /** Whether this option is disabled. */
  isDisabled?: boolean;
}

/** A single option. Mirrors `<option value=…>label</option>`. */
export function SelectOption({ label, value, isDisabled, ...rest }: SelectOptionProps) {
  return (
    <ListBoxItem id={value ?? label} isDisabled={isDisabled} {...rest}>
      {label}
    </ListBoxItem>
  );
}

interface UISelectProps extends Omit<
  SelectProps<object>,
  'children' | 'value' | 'defaultValue' | 'onChange'
> {
  /** Selected value (string). */
  value?: string;
  /** Default value for uncontrolled usage. */
  defaultValue?: string;
  /** Called with the selected value (string) when the user changes it. */
  onChange?: (value: string) => void;
  /** Class applied to the trigger button (defaults to form-select). */
  inputClassName?: string;
  /** aria-label for the trigger (screen readers). */
  'aria-label'?: string;
  /** Placeholder shown when no value is selected. */
  placeholder?: string;
  /** Whether the control is disabled. */
  disabled?: boolean;
  children: React.ReactNode;
}

/**
 * Drop-in for `<select className="form-select" value={} onChange={} />`.
 *
 * Children are `<SelectOption label=… value=… />` elements (or any
 * ListBoxItem-compatible children). The trigger is styled with `form-select`.
 */
export function Select({
  value,
  defaultValue,
  onChange,
  inputClassName,
  placeholder,
  disabled,
  children,
  ...rest
}: UISelectProps) {
  return (
    <AriaSelect
      {...rest}
      isDisabled={disabled}
      placeholder={placeholder ?? '— 选择 —'}
      selectedKey={value ?? null}
      defaultSelectedKey={defaultValue}
      onSelectionChange={(key) => {
        if (key == null) onChange?.('');
        else onChange?.(String(key));
      }}
    >
      {({ isInvalid }) => (
        <>
          <Button
            className={cn('form-select text-left', inputClassName)}
            aria-label={rest['aria-label']}
          >
            {/* No children: React Aria renders the selected item's text, or
                the Select's placeholder when nothing is selected. Passing a
                plain string as children would mask the selected value. */}
            <SelectValue />
          </Button>
          <Popover
            offset={4}
            className="min-w-[--trigger-width] overflow-hidden rounded-md border border-border bg-surface py-1 shadow-lg outline-none"
          >
            <ListBox className="max-h-[260px] overflow-auto outline-none">{children}</ListBox>
          </Popover>
          {isInvalid && <span className="sr-only">无效值</span>}
        </>
      )}
    </AriaSelect>
  );
}
