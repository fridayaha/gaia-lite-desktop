/**
 * Project-level IME-safe input components.
 *
 * Background (facebook/react#8683): React's controlled `<input value={state}
 * onChange={setState}>` can interrupt CJK IME composition — React 19 may fire
 * onChange mid-composition, and the resulting re-render writes the controlled
 * value (which the parent hasn't updated yet, since the composition isn't
 * committed) back to the DOM, closing the candidate window. The result: the
 * user types Chinese but the value "doesn't go in".
 *
 * Fix (most robust): make the input UNCONTROLLED during IME composition. While
 * composing, the DOM manages its own value (no React write-back), so the IME
 * candidate window is never disturbed. When not composing, the input is
 * controlled as normal. We detect composition via onCompositionStart/End and
 * switch the `value`/`defaultValue` props accordingly. The parent's onChange
 * still fires (React defers it to after compositionend), so state syncs once
 * the user commits the Chinese characters.
 *
 * See ADR-013.
 */
import { useState, type ComponentProps, type CSSProperties } from 'react';
import type React from 'react';

/** Props shared by TextInput / TextAreaInput. The wrapper accepts the full
 * native element prop set (so pattern/title/autoFocus/maxLength etc. pass
 * through), plus our own sugar: `onChange(value)` (string, not event) and
 * `inputClassName` (lets the caller keep the `form-input` class on the input
 * while using `className` for a wrapping layout if needed). */
interface IMESafeProps {
  value?: string;
  /** String-based onChange — receives the field's text value, not the event. */
  onChange?: (value: string) => void;
  defaultValue?: string;
  /** Class applied to the underlying input/textarea (defaults to form-input). */
  className?: string;
  /** Alias for className; takes precedence when both are set. */
  inputClassName?: string;
}

type TextInputProps = Omit<ComponentProps<'input'>, 'onChange' | 'value' | 'defaultValue'> &
  IMESafeProps;

type TextAreaInputProps = Omit<ComponentProps<'textarea'>, 'onChange' | 'value' | 'defaultValue'> &
  IMESafeProps & { rows?: number; style?: CSSProperties };

/**
 * Single-line text input, IME-safe. Drop-in for
 * `<input className="form-input" value={} onChange={} />`.
 */
export function TextInput({
  value,
  defaultValue,
  onChange,
  className,
  inputClassName,
  ...rest
}: TextInputProps) {
  // While composing, render uncontrolled (defaultValue) so React never writes
  // a stale value back mid-IME. The DOM keeps the live composition text.
  const [composing, setComposing] = useState(false);

  return (
    <input
      {...rest}
      className={inputClassName ?? className ?? 'form-input'}
      // Uncontrolled (value=undefined) while composing — React won't write a
      // stale value back mid-IME. Controlled again once composition ends.
      value={composing ? undefined : (value ?? '')}
      defaultValue={defaultValue}
      onChange={(e: React.ChangeEvent<HTMLInputElement>) => onChange?.(e.target.value)}
      onCompositionStart={() => setComposing(true)}
      onCompositionEnd={(e: React.CompositionEvent<HTMLInputElement>) => {
        setComposing(false);
        // Flush the composed value to the parent; React's own onChange may
        // also fire after this (Chrome v53+), but the parent's setState is
        // idempotent for the same value.
        onChange?.(e.currentTarget.value);
      }}
    />
  );
}

/**
 * Multi-line text area, IME-safe. Drop-in for
 * `<textarea className="form-input" value={} onChange={} />`.
 */
export function TextAreaInput({
  value,
  defaultValue,
  onChange,
  className,
  inputClassName,
  rows = 2,
  style,
  ...rest
}: TextAreaInputProps) {
  const [composing, setComposing] = useState(false);

  return (
    <textarea
      {...rest}
      className={inputClassName ?? className ?? 'form-input resize-none'}
      value={composing ? undefined : (value ?? '')}
      defaultValue={defaultValue}
      rows={rows}
      style={style}
      onChange={(e: React.ChangeEvent<HTMLTextAreaElement>) => onChange?.(e.target.value)}
      onCompositionStart={() => setComposing(true)}
      onCompositionEnd={(e: React.CompositionEvent<HTMLTextAreaElement>) => {
        setComposing(false);
        onChange?.(e.currentTarget.value);
      }}
    />
  );
}
