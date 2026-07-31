/**
 * Project-level read-only data table (ADR-013).
 *
 * Originally built on React Aria Components' `Table` for keyboard row/cell
 * navigation + screen-reader landmarks. **Reverted to a native `<table>` in
 * 2026-07** because React Aria's collection system throws
 * `Cell count must match column count` when the table is unmounted and
 * remounted in quick succession (e.g. clicking through tables in the data
 * source explorer). This is a long-standing React Aria defect
 * (adobe/react-spectrum#8127 / #9937 / #8906) with no upstream fix, and the
 * `key`-remount workaround is insufficient because the race happens *during*
 * the remount itself. None of the call sites actually use the keyboard
 * navigation / row-selection features that justified React Aria `Table`, so a
 * native semantic `<table>` preserves every behaviour callers rely on while
 * removing the crash. See ADR-013 revision note.
 *
 * The component keeps the same props surface (columns / rows / renderCell /
 * rowKey / rowClassName / onRowAction / aria-label) so call sites are
 * unaffected.
 *
 * For interactive tables that embed form controls in cells, prefer a native
 * `<table>` directly (this wrapper targets read-only display tables like
 * previews and read-only lists).
 */
import type { KeyboardEvent, ReactNode } from 'react';
import { cn } from '../../lib/cn';

export interface DataTableColumn {
  /** Unique column id. */
  id: string;
  /** Header label (string or rich content, e.g. label + inline action icon). */
  label: ReactNode;
  /** Optional cell class. */
  cellClassName?: string;
}

interface DataTableProps<T> {
  columns: DataTableColumn[];
  rows: T[];
  /** Render a cell's value for a given row + column id. */
  renderCell: (row: T, columnId: string) => React.ReactNode;
  /** Accessor for the row's unique key (defaults to the row index). */
  rowKey?: (row: T, index: number) => string | number;
  /** Optional class on the table element. */
  className?: string;
  /** Optional per-row class. */
  rowClassName?: (row: T) => string | undefined;
  /** Optional row click handler (enables row keyboard activation). */
  onRowAction?: (key: string) => void;
  /** Accessible label for the table. */
  'aria-label'?: string;
}

export function DataTable<T>({
  columns,
  rows,
  renderCell,
  rowKey,
  className,
  rowClassName,
  onRowAction,
  ...rest
}: DataTableProps<T>) {
  const interactive = typeof onRowAction === 'function';

  function handleRowKeyDown(e: KeyboardEvent<HTMLTableRowElement>, key: string) {
    if (!onRowAction) return;
    // Mirror React Aria's row activation semantics: Enter / Space triggers the action.
    if (e.key === 'Enter' || e.key === ' ') {
      e.preventDefault();
      onRowAction(key);
    }
  }

  return (
    <table
      aria-label={rest['aria-label'] ?? '数据表'}
      className={cn('data-table w-full border-collapse text-sm outline-none', className)}
    >
      <thead>
        <tr>
          {columns.map((col) => (
            <th key={col.id} scope="col" className={col.cellClassName}>
              {col.label}
            </th>
          ))}
        </tr>
      </thead>
      <tbody>
        {rows.map((row, i) => {
          const key = rowKey ? String(rowKey(row, i)) : String(i);
          return (
            <tr
              key={key}
              className={cn(rowClassName?.(row))}
              onClick={interactive ? () => onRowAction!(key) : undefined}
              onKeyDown={interactive ? (e) => handleRowKeyDown(e, key) : undefined}
              tabIndex={interactive ? 0 : undefined}
              role={interactive ? 'button' : undefined}
            >
              {columns.map((col) => (
                <td key={col.id} className={cn(col.cellClassName)}>
                  {renderCell(row, col.id)}
                </td>
              ))}
            </tr>
          );
        })}
      </tbody>
    </table>
  );
}
