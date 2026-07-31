import type { ColumnInfo } from '../types';
import { cn } from '../lib/cn';
import { DataTable, type DataTableColumn } from './ui/DataTable';

interface ColumnListProps {
  columns: ColumnInfo[];
  /** Compact mode: single line per column (tree view). Default: table mode. */
  compact?: boolean;
  /** Allow single-column selection (incremental column picker). */
  selectable?: boolean;
  selectedColumn?: string | null;
  onColumnSelect?: (columnName: string) => void;
  /** Highlight FK columns and show target tables. */
  highlightFK?: boolean;
  fkTargets?: Record<string, string>;
}

export function ColumnList({
  columns,
  compact = false,
  selectable = false,
  selectedColumn,
  onColumnSelect,
  highlightFK = false,
  fkTargets,
}: ColumnListProps) {
  if (compact) {
    return (
      <div className="column-list-compact">
        {columns.map((col) => {
          const isPk = col.is_primary_key;
          const fkTarget = highlightFK ? fkTargets?.[col.name] : undefined;
          const isSelected = selectable && selectedColumn === col.name;
          return (
            <div
              key={col.name}
              className={cn('column-row', selectable && 'selectable', isSelected && 'selected')}
              onClick={() => selectable && onColumnSelect?.(col.name)}
            >
              <span className="column-name">{col.name}</span>
              <span className="column-type">{col.data_type}</span>
              {isPk && <span className="column-pk">PK</span>}
              {fkTarget && <span className="column-fk">→ {fkTarget}</span>}
              {col.nullable ? (
                <span className="column-null">NULL</span>
              ) : (
                <span className="column-null">NOT NULL</span>
              )}
            </div>
          );
        })}
      </div>
    );
  }

  // Table mode
  const tableColumns: DataTableColumn[] = [
    { id: 'name', label: '列名' },
    { id: 'type', label: '类型' },
    { id: 'null', label: 'NULL', cellClassName: 'w-[50px]' },
    { id: 'pk', label: '主键', cellClassName: 'w-[40px]' },
    ...(highlightFK ? [{ id: 'fk', label: '外键', cellClassName: 'w-[80px]' }] : []),
    { id: 'comment', label: '说明' },
  ];
  return (
    <DataTable
      aria-label="列列表"
      columns={tableColumns}
      rows={columns}
      rowKey={(c) => c.name}
      rowClassName={(c) =>
        cn(
          selectable && 'cursor-pointer',
          selectable && selectedColumn === c.name && 'bg-[var(--accent-bg)]',
        )
      }
      onRowAction={selectable && onColumnSelect ? (key) => onColumnSelect(key) : undefined}
      renderCell={(col, colId) => {
        const fkTarget = highlightFK ? fkTargets?.[col.name] : undefined;
        if (colId === 'name') return <span className="font-mono font-medium">{col.name}</span>;
        if (colId === 'type')
          return <span className="font-mono text-xs text-text-secondary">{col.data_type}</span>;
        if (colId === 'null')
          return (
            <span
              className={cn(
                'text-center block',
                col.nullable ? 'text-text-muted' : 'text-text-secondary',
              )}
            >
              {col.nullable ? '✓' : '✗'}
            </span>
          );
        if (colId === 'pk')
          return (
            <span className="text-center block">
              {col.is_primary_key ? <span className="text-accent-text">✓</span> : ''}
            </span>
          );
        if (colId === 'fk') return <span className="text-xs text-teal">{fkTarget || '-'}</span>;
        return (
          <span className="block max-w-[200px] overflow-hidden text-ellipsis text-xs text-text-muted">
            {col.comment || ''}
          </span>
        );
      }}
    />
  );
}
