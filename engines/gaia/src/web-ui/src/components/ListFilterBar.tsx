/**
 * 通用列表筛选栏（ADR-013 · CLAUDE.md 第二原则组件复用）。
 *
 * 数据源页与数据集页共享。内聚搜索防抖（200ms），支持单选下拉 +
 * 多组 chips 多选（每组独立一行，作为二级筛选）。所有筛选项可选；
 * 不传则不渲染对应区块。
 */
import { useEffect, useState } from 'react';
import { cn } from '../lib/cn';
import { Select, SelectOption } from './ui/Select';

export interface ChipOption {
  label: string;
  value: string;
  count?: number;
}

export interface ChipGroup {
  /** 组标签（如「类型」「本体」），显示为前缀。 */
  label: string;
  options: ChipOption[];
  /** 当前选中的 value 列表（多选）。 */
  selected: string[];
  onChange: (vals: string[]) => void;
}

export interface SelectFilter {
  label: string;
  value: string;
  options: { label: string; value: string }[];
  onChange: (v: string) => void;
}

interface ListFilterBarProps {
  searchValue: string;
  onSearchChange: (v: string) => void;
  searchPlaceholder?: string;
  /** 单选下拉筛选（如状态）。 */
  selects?: SelectFilter[];
  /** 多组 chips 二级筛选，每组独占一行（如类型、本体）。 */
  chipGroups?: ChipGroup[];
}

export function ListFilterBar({
  searchValue,
  onSearchChange,
  searchPlaceholder = '搜索…',
  selects,
  chipGroups,
}: ListFilterBarProps) {
  // 本地输入态 + 防抖回传，避免每次按键触发列表重渲染。
  // searchValue 仅作初始值；父组件不主动重置搜索词，故无需同步 effect。
  const [input, setInput] = useState(searchValue);

  useEffect(() => {
    const t = setTimeout(() => {
      onSearchChange(input);
    }, 200);
    return () => clearTimeout(t);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [input]);

  function toggleChip(group: ChipGroup, val: string) {
    const next = group.selected.includes(val)
      ? group.selected.filter((v) => v !== val)
      : [...group.selected, val];
    group.onChange(next);
  }

  const hasSelects = selects && selects.length > 0;
  const groups = (chipGroups || []).filter((g) => g.options.length > 0);

  return (
    <div className="mb-4 flex flex-wrap items-center gap-2 rounded-md border border-border bg-surface px-3 py-2">
      {/* 搜索 */}
      <input
        type="search"
        className="form-input min-w-[180px] flex-1 px-2 py-1 text-sm"
        placeholder={searchPlaceholder}
        value={input}
        onChange={(e) => setInput(e.target.value)}
        aria-label="搜索"
      />

      {/* 单选下拉 */}
      {hasSelects &&
        selects!.map((s) => (
          <div key={s.label} className="flex items-center gap-1">
            <span className="text-[11px] text-text-muted">{s.label}</span>
            <Select
              inputClassName="form-input w-[110px] px-1.5 py-0.5 text-xs"
              value={s.value}
              onChange={s.onChange}
              aria-label={s.label}
            >
              {s.options.map((o) => (
                <SelectOption key={o.value} value={o.value} label={o.label} />
              ))}
            </Select>
          </div>
        ))}

      {/* 多组 chips 二级筛选，每组独立一行 */}
      {groups.map((group) => (
        <div key={group.label} className="flex w-full flex-wrap items-center gap-1.5 pt-1">
          <span className="text-[11px] text-text-muted">{group.label}:</span>
          {group.options.map((c) => {
            const active = group.selected.includes(c.value);
            return (
              <button
                key={c.value}
                className={cn(
                  'rounded-full border px-2 py-0.5 text-[11px] transition-colors',
                  active
                    ? 'border-accent bg-[var(--accent-bg)] text-accent-text'
                    : 'border-border text-text-secondary hover:bg-white/[0.03]',
                )}
                onClick={() => toggleChip(group, c.value)}
                aria-pressed={active}
              >
                {c.label}
                {c.count != null && <span className="ml-1 text-[10px] opacity-60">{c.count}</span>}
              </button>
            );
          })}
          {group.selected.length > 0 && (
            <button
              className="text-[11px] text-text-muted underline decoration-dotted"
              onClick={() => group.onChange([])}
            >
              清除
            </button>
          )}
        </div>
      ))}
    </div>
  );
}
