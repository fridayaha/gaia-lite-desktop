import { useState, useMemo } from 'react';
import { SearchBar } from './SearchBar';

import { cn } from '../lib/cn';
import type { TableInfo } from '../types';

export interface SchemaNode {
  schema_name: string;
  tables: TableInfo[];
}

interface SchemaTreeBrowserProps {
  schemas: SchemaNode[];
  searchable?: boolean;
  onTableClick?: (tableName: string) => void;
  selectedTable?: string | null;
  /** Lazily loaded column data keyed by table name. Merged with table.columns. */
  columnMap?: Record<string, TableInfo>;
}

/**
 * Pure-navigation schema tree — click a table name to preview it in the
 * detail panel. No checkboxes, no multi-select, no batch operations.
 *
 * Matches the industry-standard pattern (Snowflake / Databricks / Deepnote):
 * schema browser is for browsing, not for bulk selection.
 */
export function SchemaTreeBrowser({
  schemas,
  searchable = true,
  onTableClick,
  selectedTable,
  columnMap = {},
}: SchemaTreeBrowserProps) {
  const [search, setSearch] = useState('');

  const filtered = useMemo(() => {
    if (!search) return schemas;
    const q = search.toLowerCase();
    return schemas
      .map((s) => ({
        ...s,
        tables: s.tables.filter(
          (t) =>
            t.name.toLowerCase().includes(q) ||
            (columnMap[t.name]?.columns || t.columns).some(
              (c) => c.name.toLowerCase().includes(q) || c.data_type.toLowerCase().includes(q),
            ),
        ),
      }))
      .filter((s) => s.tables.length > 0);
  }, [schemas, search, columnMap]);

  return (
    <div className="schema-tree-wrap">
      {searchable && (
        <SearchBar value={search} onChange={setSearch} placeholder="搜索表名或列名…" />
      )}
      <div className="schema-tree">
        {filtered.length === 0 && search ? (
          <div className="px-2 py-3 text-center text-xs text-text-muted">
            未找到匹配 "{search}" 的表
          </div>
        ) : (
          filtered.map((schema) => (
            <div key={schema.schema_name}>
              <div className="schema-schema-label">
                📁 {schema.schema_name}
                <span className="ml-1 font-normal">({schema.tables.length})</span>
              </div>
              {schema.tables.map((table) => (
                <div
                  key={table.name}
                  className={cn(
                    'schema-table-node',
                    selectedTable === table.name && 'selected',
                  )}
                  onClick={() => onTableClick?.(table.name)}
                >
                  <span className="schema-table-icon">⊡</span>
                  <span className="schema-table-name">{table.name}</span>
                </div>
              ))}
            </div>
          ))
        )}
      </div>
    </div>
  );
}
