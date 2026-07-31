/**
 * Project-level DatePicker wrapper around React Aria Components
 * (ADR-013 Phase 5).
 *
 * React Aria's DatePicker works with `DateValue` objects (CalendarDate from
 * @internationalized/date). This wrapper adapts it to a **string** value
 * (ISO `YYYY-MM-DD`) so call sites keep a simple `value`/`onChange(string)`
 * API, matching the rest of the form (and the backend's date string format).
 *
 * See ADR-013.
 */
import { CalendarDate, parseDate } from '@internationalized/date';
import {
  DatePicker as AriaDatePicker,
  DateInput,
  DateSegment,
  Calendar,
  CalendarGrid,
  CalendarGridHeader,
  CalendarGridBody,
  CalendarHeaderCell,
  CalendarCell,
  Popover,
  Button,
  type DatePickerProps,
} from 'react-aria-components';
import { cn } from '../../lib/cn';

interface UIDatePickerProps extends Omit<
  DatePickerProps<CalendarDate>,
  'value' | 'onChange' | 'defaultValue'
> {
  /** Selected date as ISO `YYYY-MM-DD` string (or empty). */
  value?: string;
  /** Called with the selected date as ISO `YYYY-MM-DD` string. */
  onChange?: (value: string) => void;
  /** Class applied to the field group. */
  className?: string;
  /** aria-label for the field. */
  'aria-label'?: string;
  /** Whether the control is disabled. */
  disabled?: boolean;
}

function toCalendarDate(iso: string | undefined): CalendarDate | null {
  if (!iso) return null;
  try {
    return parseDate(iso);
  } catch {
    return null;
  }
}

export function DatePicker({ value, onChange, className, disabled, ...rest }: UIDatePickerProps) {
  const selected = toCalendarDate(value);
  return (
    <AriaDatePicker
      {...rest}
      isDisabled={disabled}
      value={selected ?? undefined}
      onChange={(v) => {
        if (!v) onChange?.('');
        else onChange?.(v.toString());
      }}
      className={cn('inline-flex flex-col gap-1', className)}
    >
      <div className="flex">
        <DateInput className="form-input flex items-center gap-1 data-[focus-within]:border-accent">
          {(segment) => <DateSegment segment={segment} className="px-0.5 outline-none" />}
        </DateInput>
        <Button
          className="ml-[-28px] inline-flex items-center px-2 text-text-muted"
          aria-label="选择日期"
        >
          📅
        </Button>
      </div>
      <Popover className="min-w-[--trigger-width] overflow-hidden rounded-md border border-border bg-surface shadow-lg outline-none">
        <Calendar className="p-3 outline-none">
          <CalendarGrid>
            <CalendarGridHeader>
              {() => <CalendarHeaderCell className="px-1 text-[10px] text-text-muted" />}
            </CalendarGridHeader>
            <CalendarGridBody>
              {(date) => (
                <CalendarCell
                  date={date}
                  className="mx-auto flex h-7 w-7 items-center justify-center rounded text-xs outline-none data-[hovered]:bg-[var(--accent-bg)] data-[selected]:bg-accent data-[selected]:text-white"
                />
              )}
            </CalendarGridBody>
          </CalendarGrid>
        </Calendar>
      </Popover>
    </AriaDatePicker>
  );
}
