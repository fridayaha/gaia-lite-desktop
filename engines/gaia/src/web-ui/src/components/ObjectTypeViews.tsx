import { useState, useMemo } from 'react';
import type { ObjectTypeSummary, LinkTypeDef } from '../types';
import { cn } from '../lib/cn';
import { TextInput } from './ui/TextField';

// ══════════════════════════════════════════════════════════
// ObjectTypeCard — reusable card for a single object type
// ══════════════════════════════════════════════════════════

export function ObjectTypeCard({
  ot,
  links,
  selected,
  onSelect,
  onEdit,
  onDelete,
}: {
  ot: ObjectTypeSummary;
  links: LinkTypeDef[];
  selected: boolean;
  onSelect: () => void;
  onEdit: () => void;
  onDelete: () => void;
}) {
  const relationCount = links.filter(
    (l) => l.source_object_type_id === ot.id || l.target_object_type_id === ot.id,
  ).length;

  return (
    <div className={cn('card', 'object-card', selected && 'selected')} onClick={onSelect}>
      <div className="object-card-header">
        <span className="object-card-name">{ot.display_name}</span>
        <span className={cn('object-card-status', `status-${ot.status.toLowerCase()}`)}>
          {ot.storage_type === 'VIRTUAL' ? '虚拟' : '托管'}
        </span>
      </div>
      <div className="object-card-meta">
        <span>{ot.properties_count} 属性</span>
        <span>{relationCount} 关系</span>
      </div>
      <div className="mt-2 flex gap-2">
        <button
          className="btn btn-sm"
          onClick={(e) => {
            e.stopPropagation();
            onEdit();
          }}
        >
          编辑
        </button>
        <button
          className="btn btn-sm"
          onClick={(e) => {
            e.stopPropagation();
            onDelete();
          }}
        >
          删除
        </button>
      </div>
    </div>
  );
}

// ══════════════════════════════════════════════════════════
// CardView — card grid with scroll container for large lists
// ══════════════════════════════════════════════════════════

export function CardView({
  objectTypes,
  links,
  selectedObjectType,
  onSelect,
  onEdit,
  onDelete,
}: {
  objectTypes: ObjectTypeSummary[];
  links: LinkTypeDef[];
  selectedObjectType: string | null;
  onSelect: (name: string) => void;
  onEdit: (ot: ObjectTypeSummary) => void;
  onDelete: (name: string, displayName: string) => void;
}) {
  const content = (
    <div className="card-grid">
      {[...objectTypes]
        .sort((a, b) => a.display_name.localeCompare(b.display_name, 'zh', { sensitivity: 'base' }))
        .map((ot) => (
          <ObjectTypeCard
            key={ot.id}
            ot={ot}
            links={links}
            selected={selectedObjectType === ot.api_name}
            onSelect={() => onSelect(ot.api_name)}
            onEdit={() => onEdit(ot)}
            onDelete={() => onDelete(ot.api_name, ot.display_name)}
          />
        ))}
    </div>
  );

  if (objectTypes.length > 20) {
    return <div className="max-h-[calc(100vh-260px)] overflow-auto">{content}</div>;
  }
  return content;
}

// ══════════════════════════════════════════════════════════
// TableView — searchable, sortable, paginated table
// ══════════════════════════════════════════════════════════

type SortColumn = 'display_name' | 'api_name' | 'properties' | 'storage_type';

export function TableView({
  objectTypes,
  links,
  selectedObjectType,
  onSelect,
  onEdit,
  onDelete,
}: {
  objectTypes: ObjectTypeSummary[];
  links: LinkTypeDef[];
  selectedObjectType: string | null;
  onSelect: (name: string) => void;
  onEdit: (ot: ObjectTypeSummary) => void;
  onDelete: (name: string, displayName: string) => void;
}) {
  const [search, setSearch] = useState('');
  const [page, setPage] = useState(1);
  const [sortBy, setSortBy] = useState<SortColumn>('display_name');
  const [sortDir, setSortDir] = useState<'asc' | 'desc'>('asc');
  const pageSize = 20;

  const filtered = useMemo(() => {
    let list = objectTypes;
    if (search.trim()) {
      const q = search.toLowerCase();
      list = list.filter(
        (ot) => ot.display_name.toLowerCase().includes(q) || ot.api_name.toLowerCase().includes(q),
      );
    }
    list = [...list].sort((a, b) => {
      const col = sortBy === 'properties' ? null : sortBy;
      const va: string | number = col ? (a[col] ?? '') : a.properties_count;
      const vb: string | number = col ? (b[col] ?? '') : b.properties_count;
      let cmp: number;
      if (typeof va === 'string' && typeof vb === 'string') {
        // 与 CardView 一致：中文 locale 排序（大小写不敏感）
        cmp = va.localeCompare(vb, 'zh', { sensitivity: 'base' });
      } else {
        cmp = va < vb ? -1 : va > vb ? 1 : 0;
      }
      return cmp * (sortDir === 'asc' ? 1 : -1);
    });
    return list;
  }, [objectTypes, search, sortBy, sortDir]);

  const totalPages = Math.max(1, Math.ceil(filtered.length / pageSize));
  const paged = filtered.slice((page - 1) * pageSize, page * pageSize);

  const handleSearchChange = (value: string) => {
    setSearch(value);
    setPage(1);
  };

  const handleSort = (col: SortColumn) => {
    setSortDir(sortBy === col && sortDir === 'asc' ? 'desc' : 'asc');
    setSortBy(col);
  };

  if (objectTypes.length === 0) {
    return (
      <div className="empty-state mt-10">
        <h2>还没有对象</h2>
        <p>点击"+ 新建对象"开始</p>
      </div>
    );
  }

  return (
    <div>
      <div className="mb-3 flex gap-2">
        <TextInput
          inputClassName="form-input max-w-[320px] flex-1"
          placeholder="🔍 搜索对象名称或 API name..."
          value={search}
          onChange={handleSearchChange}
        />
        {search && (
          <span className="self-center text-xs text-text-muted">
            匹配 {filtered.length}/{objectTypes.length}
          </span>
        )}
      </div>

      <div className="overflow-hidden rounded-md border border-border">
        <table className="data-table m-0">
          <thead>
            <tr>
              {(['display_name', 'api_name', 'properties', null, null, 'storage_type'] as const).map(
                (col, i) => {
                  if (!col) {
                    // Two null slots: first = 关系, second = 动作.
                    const label = i === 3 ? '关系' : '动作';
                    return (
                      <th key={i} className="w-[60px] text-center">
                        {label}
                      </th>
                    );
                  }
                  return (
                    <th
                      key={col}
                      className={cn(
                        'cursor-pointer',
                        (col === 'properties' || col === 'storage_type') && 'text-center',
                      )}
                      onClick={() => handleSort(col)}
                    >
                      {col === 'display_name'
                        ? '名称'
                        : col === 'api_name'
                          ? 'API Name'
                          : col === 'properties'
                            ? '属性'
                            : '类型'}
                      <SortIndicator col={col} sortBy={sortBy} sortDir={sortDir} />
                    </th>
                  );
                },
              )}
              <th className="w-[120px] text-right">操作</th>
            </tr>
          </thead>
          <tbody>
            {paged.map((ot) => {
              const relationCount = links.filter(
                (l) => l.source_object_type_id === ot.id || l.target_object_type_id === ot.id,
              ).length;
              return (
                <tr
                  key={ot.id}
                  onClick={() => onSelect(ot.api_name)}
                  className={cn(
                    'cursor-pointer',
                    selectedObjectType === ot.api_name && 'bg-[var(--accent-bg)]',
                  )}
                >
                  <td className="font-medium">{ot.display_name}</td>
                  <td className="font-mono text-xs">{ot.api_name}</td>
                  <td className="text-center">{ot.properties_count}</td>
                  <td className="text-center">{relationCount}</td>
                  <td className="text-center">{ot.actions_count}</td>
                  <td className="text-center">
                    <span
                      className={cn(
                        'object-card-status',
                        `status-${ot.storage_type === 'VIRTUAL' ? 'experimental' : 'active'}`,
                      )}
                    >
                      {ot.storage_type === 'VIRTUAL' ? '虚拟' : '托管'}
                    </span>
                  </td>
                  <td className="text-right">
                    <button
                      className="btn btn-sm"
                      onClick={(e) => {
                        e.stopPropagation();
                        onEdit(ot);
                      }}
                    >
                      编辑
                    </button>
                    <button
                      className="btn btn-sm ml-1"
                      onClick={(e) => {
                        e.stopPropagation();
                        onDelete(ot.api_name, ot.display_name);
                      }}
                    >
                      删除
                    </button>
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>

      {totalPages > 1 && (
        <div className="mt-3 flex items-center justify-between text-xs text-text-muted">
          <span>共 {filtered.length} 条</span>
          <div className="flex gap-1">
            <button className="btn btn-sm" disabled={page <= 1} onClick={() => setPage(page - 1)}>
              ←
            </button>
            {paginationRange(page, totalPages).map((p) => (
              <button
                key={p}
                className={cn('btn', 'btn-sm', p === page && 'btn-primary', 'min-w-[28px]')}
                onClick={() => setPage(p)}
              >
                {p}
              </button>
            ))}
            <button
              className="btn btn-sm"
              disabled={page >= totalPages}
              onClick={() => setPage(page + 1)}
            >
              →
            </button>
          </div>
        </div>
      )}
    </div>
  );
}

// ── Helpers ──

function SortIndicator({
  col,
  sortBy,
  sortDir,
}: {
  col: SortColumn;
  sortBy: SortColumn;
  sortDir: string;
}) {
  if (sortBy !== col) return <span className="opacity-20"> ↕</span>;
  return <span>{sortDir === 'asc' ? ' ↑' : ' ↓'}</span>;
}

function paginationRange(page: number, total: number): number[] {
  if (total <= 7) return Array.from({ length: total }, (_, i) => i + 1);
  if (page <= 4) return [1, 2, 3, 4, 5, 6, 7];
  if (page >= total - 3) return Array.from({ length: 7 }, (_, i) => total - 6 + i);
  return [page - 3, page - 2, page - 1, page, page + 1, page + 2, page + 3];
}
