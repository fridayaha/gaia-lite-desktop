/**
 * Project-level ComboBox wrapper around React Aria Components (ADR-013 Phase 5).
 *
 * A searchable single-select with a free-form text input and a dropdown
 * listbox. Use for datasets / object search where the user may type to filter
 * and pick from a list. Distinct from `Select` (no typing) and from a free-text
 * `TextInput` (no list).
 *
 * Two modes:
 *  - **constrained** (default): the value must be one of `options`. The wrapper
 *    drives `selectedKey` from `value` and calls `onChange(value)` on pick.
 *  - **free-form** (`allowsCustomValue`): the user may also type an arbitrary
 *    string not in the list (e.g. a primary key for a large object set). The
 *    caller observes the typed/selected value via `onChange` (selection) and
 *    `onInputChange` (typing).
 *
 * **Async / server-side search**: pass `items`-backed `options` (the caller
 * updates `options` as search results arrive), `loadingState` ('loading'/
 * 'filtering' while fetching), `allowsEmptyCollection`, and `menuTrigger`/
 * `onOpenChange`/`isOpen` to drive the popover. The wrapper uses React Aria's
 * dynamic-collection pattern (`items` prop + render-function children) which
 * is required for async items to update the popover correctly — static
 * children don't (react-spectrum#5234).
 *
 * The input text is intentionally **uncontrolled**. React Aria's combobox only
 * auto-closes the popover on listbox selection when `inputValue` is not
 * controlled (its close-on-select effect compares the controlled display value
 * against the last value); keeping it uncontrolled lets selection close the
 * list and auto-sync the input to the chosen item's label. Callers that need
 * to observe the typed text use `onInputChange`.
 *
 * See ADR-013.
 */
import {
  ComboBox as AriaComboBox,
  Input,
  Button,
  Popover,
  ListBox,
  ListBoxItem,
  type ComboBoxProps,
} from 'react-aria-components';
import { cn } from '../../lib/cn';

export interface ComboBoxOption {
  value: string;
  /** Label used for filtering and the default trigger display. */
  label: string;
  /** Optional rich content rendered inside the listbox item (overrides label). */
  content?: React.ReactNode;
}

interface UIComboBoxProps extends Omit<
  ComboBoxProps<object>,
  'children' | 'value' | 'onChange' | 'items' | 'inputValue' | 'onInputChange'
> {
  options: ComboBoxOption[];
  /** Selected key (string). In constrained mode this is the value. */
  value?: string;
  /** Default value for uncontrolled usage. */
  defaultValue?: string;
  /**
   * Called with the selected value (string) when the user picks an option
   * (both modes). In `allowsCustomValue` mode, free-form typing is observed
   * via `onInputChange`.
   */
  onChange?: (value: string) => void;
  /** Class applied to the input (defaults to form-input). */
  inputClassName?: string;
  /** aria-label for the combo (screen readers). */
  'aria-label'?: string;
  /** Placeholder for the input. */
  placeholder?: string;
  /** Whether the control is disabled. */
  disabled?: boolean;
  /** Custom filter: returns the option label to match against (defaults to
   *  label + value). */
  getFilterText?: (option: ComboBoxOption) => string;

  // ── Free-form mode (`allowsCustomValue`) ──
  /** Allow typing a value not present in `options` (e.g. a raw primary key). */
  allowsCustomValue?: boolean;
  /** Default input text (uncontrolled). React Aria auto-syncs the input to the
   *  selected item's label on selection. */
  defaultInputValue?: string;
  /** Called when the input text changes (free-form mode). */
  onInputChange?: (value: string) => void;
  /** When the menu opens: "input" (default, type to open), "focus" (open on
   *  focus), "manual". ObjectPicker uses "focus" to show candidates as soon
   *  as the user enters the field. */
  menuTrigger?: 'input' | 'focus' | 'manual';

  // ── Async / server-side search ──
  /** Controlled popover open state. */
  isOpen?: boolean;
  /** Async loading state. When 'loading'/'filtering', the popover shows a
   *  "搜索中…" empty state. (react-aria-components ComboBox has no
   *  loadingState prop — we drive the empty-state text from this.) */
  loadingState?: 'loading' | 'filtering' | 'idle';
  /** Allow the popover to show even when the item list is empty (shows the
   *  empty/loading state). Required for async search where the list starts
   *  empty and fills in. */
  allowsEmptyCollection?: boolean;
  /** Filtering mode:
   *  - `'client'` (default): local data — React Aria filters with
   *    `getFilterText`/`defaultFilter` and auto-syncs the input to the
   *    selected label on pick. Static `<ListBoxItem>` children are rendered.
   *  - `'async'`: server-side search — the caller passes already-filtered
   *    `options` + `loadingState`/`isOpen`/`onOpenChange`. Dynamic-collection
   *    render function is used so async items update the popover (react-
   *    spectrum#5234). React Aria does NOT apply defaultFilter (assumes the
   *    caller filtered).
   *
   *  The mode is explicit because it can't always be inferred: a local list
   *  may pass `onOpenChange` (to reset the input on open) without being async. */
  filtering?: 'client' | 'async';
}

/**
 * Searchable single-select. Drop-in for a search input + dropdown list.
 */
export function ComboBox({
  options,
  value,
  defaultValue,
  onChange,
  inputClassName,
  placeholder,
  disabled,
  getFilterText,
  allowsCustomValue,
  defaultInputValue,
  onInputChange,
  menuTrigger,
  isOpen,
  loadingState,
  allowsEmptyCollection,
  filtering = 'client',
  ...rest
}: UIComboBoxProps) {
  // Build the dynamic-collection item list. Using `items` + a render-function
  // child (not static children) is the React Aria async pattern: it lets React
  // Aria manage items arriving asynchronously and keeps the popover open
  // across updates (react-spectrum#5234).
  //
  // BUT: passing `items` makes React Aria skip `defaultFilter` (it assumes the
  // caller already filtered — the async semantics). For local/client-side data
  // we want React Aria to filter itself AND auto-sync the input to the selected
  // label on pick (which only works in the static-collection mode). So: only
  // pass `items` when `filtering='async'`, otherwise omit it and let React Aria
  // use static children + defaultFilter. This is why OntologyContextSelector
  // (local ontology list) gets correct filter + input-sync without managing
  // query state itself.
  const isAsyncMode = filtering === 'async' || (!filtering && Boolean(loadingState || isOpen !== undefined));
  const items = options.map((o) => ({
    id: o.value,
    label: o.label,
    content: o.content,
    filterText: getFilterText ? getFilterText(o) : `${o.label} ${o.value}`,
  }));

  return (
    <AriaComboBox
      {...rest}
      isDisabled={disabled}
      allowsCustomValue={allowsCustomValue}
      allowsEmptyCollection={allowsEmptyCollection}
      selectedKey={value ?? null}
      defaultSelectedKey={defaultValue}
      onSelectionChange={(key) => {
        if (key == null) onChange?.('');
        else onChange?.(String(key));
      }}
      // Keep `inputValue` uncontrolled so React Aria auto-closes the popover
      // on selection and auto-syncs the input to the chosen label.
      defaultInputValue={defaultInputValue}
      onInputChange={(text) => onInputChange?.(text)}
      {...(isAsyncMode ? { items } : {})}
      menuTrigger={menuTrigger ?? 'input'}
      // Client-side filter. For async/server-side search the caller passes
      // already-filtered options AND should pass defaultFilter={() => true}
      // so the returned candidates aren't re-trimmed by the typed text.
      defaultFilter={(textValue, input) => {
        const item = items.find((i) => i.label === textValue);
        const haystack = item ? item.filterText : textValue;
        return haystack.toLowerCase().includes(input.toLowerCase());
      }}
    >
      {({ isInvalid }) => (
        <>
          <div className="flex">
            <Input
              className={cn('form-input', inputClassName)}
              placeholder={placeholder}
              aria-label={rest['aria-label']}
            />
            <Button
              className="ml-[-28px] inline-flex items-center px-2 text-text-muted"
              aria-label="展开"
            >
              ▾
            </Button>
          </div>
          <Popover
            offset={4}
            className="min-w-[--trigger-width] overflow-hidden rounded-md border border-border bg-surface py-1 shadow-lg outline-none"
          >
            <ListBox
              className="max-h-[260px] overflow-auto outline-none"
              renderEmptyState={() =>
                loadingState === 'loading' || loadingState === 'filtering' ? (
                  <div className="px-3 py-2 text-sm text-text-muted">搜索中…</div>
                ) : (
                  <div className="px-3 py-2 text-sm text-text-muted">无匹配项</div>
                )
              }
            >
              {/* Async mode: dynamic-collection render function (NOT static
                  children) — required for async items to update the popover
                  correctly (react-spectrum#5234).
                  Local mode: static <ListBoxItem> children — React Aria applies
                  defaultFilter itself and auto-syncs the input to the selected
                  label on pick (which only works in static-collection mode). */}
              {isAsyncMode ? (
                (item: { id: string; label: string; content?: React.ReactNode }) => (
                  <ListBoxItem
                    id={item.id}
                    textValue={item.label}
                    className="cursor-pointer px-3 py-1.5 text-sm text-text outline-none data-[hovered]:bg-[var(--accent-bg)] data-[focused]:bg-[var(--accent-bg)]"
                  >
                    {item.content ?? item.label}
                  </ListBoxItem>
                )
              ) : (
                items.map((item) => (
                  <ListBoxItem
                    key={item.id}
                    id={item.id}
                    textValue={item.label}
                    className="cursor-pointer px-3 py-1.5 text-sm text-text outline-none data-[hovered]:bg-[var(--accent-bg)] data-[focused]:bg-[var(--accent-bg)]"
                  >
                    {item.content ?? item.label}
                  </ListBoxItem>
                ))
              )}
            </ListBox>
          </Popover>
          {isInvalid && <span className="sr-only">无效值</span>}
        </>
      )}
    </AriaComboBox>
  );
}
