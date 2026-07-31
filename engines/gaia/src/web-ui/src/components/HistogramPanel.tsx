/**
 * HistogramPanel — 属性分布筛选面板（graph-reasoning-frontend-design.md §3.4 Histogram tab）。
 *
 * - 选 ObjectType → 选属性 → 显示值分布直方图（数值型分桶 / 枚举型计数）
 * - 框选区间 → "Filter to"（保留）/ "Filter out"（排除）
 * - 已应用筛选 chip 显示在顶部，点 × 移除
 */
import { useMemo, useState } from 'react';
import type { useGraphExplore } from '../hooks/useGraphExplore';
import type { GraphFilter, ObjectSetIR } from '../types';

interface HistogramPanelProps {
  explore: ReturnType<typeof useGraphExplore>;
  /** 应用筛选后重新加载（带 filter 的 IR）。 */
  onApplyFilter: (filters: GraphFilter[]) => void;
}

interface Bucket {
  label: string;
  count: number;
  /** 数值型桶的 min/max（用于 range filter）。 */
  min?: number;
  max?: number;
}

export function HistogramPanel({ explore, onApplyFilter }: HistogramPanelProps) {
  const [prop, setProp] = useState('');
  const [selectedBuckets, setSelectedBuckets] = useState<Set<string>>(new Set());
  const [filters, setFilters] = useState<GraphFilter[]>([]);

  // 收集该属性的所有值
  const values = useMemo(() => {
    if (!prop) return [];
    const vals: Array<string | number> = [];
    for (const node of explore.nodes.values()) {
      const v = node.props?.[prop];
      if (v !== undefined && v !== null) vals.push(v as string | number);
    }
    return vals;
  }, [explore.nodes, prop]);

  const isNumeric = values.length > 0 && typeof values[0] === 'number';

  // 分桶
  const buckets = useMemo<Bucket[]>(() => {
    if (values.length === 0) return [];
    if (isNumeric) {
      const nums = values as number[];
      const min = Math.min(...nums);
      const max = Math.max(...nums);
      if (min === max) return [{ label: String(min), count: nums.length, min, max }];
      const binCount = Math.min(10, Math.max(3, Math.ceil(Math.sqrt(nums.length))));
      const step = (max - min) / binCount;
      const bs: Bucket[] = [];
      for (let i = 0; i < binCount; i++) {
        const bMin = min + step * i;
        const bMax = i === binCount - 1 ? max : min + step * (i + 1);
        const count = nums.filter((n) => (i === binCount - 1 ? n >= bMin && n <= bMax : n >= bMin && n < bMax)).length;
        if (count > 0) {
          bs.push({ label: `${bMin.toFixed(1)}~${bMax.toFixed(1)}`, count, min: bMin, max: bMax });
        }
      }
      return bs;
    }
    // 枚举型计数
    const counts = new Map<string, number>();
    for (const v of values as string[]) {
      counts.set(String(v), (counts.get(String(v)) ?? 0) + 1);
    }
    return Array.from(counts.entries())
      .map(([label, count]) => ({ label, count }))
      .sort((a, b) => b.count - a.count);
  }, [values, isNumeric]);

  const maxCount = Math.max(...buckets.map((b) => b.count), 1);
  const sampleNode = explore.nodes.values().next().value;
  const propNames = sampleNode ? Object.keys(sampleNode.props) : [];

  const toggleBucket = (label: string) => {
    setSelectedBuckets((prev) => {
      const next = new Set(prev);
      if (next.has(label)) next.delete(label);
      else next.add(label);
      return next;
    });
  };

  const applyFilterTo = () => {
    if (selectedBuckets.size === 0) return;
    const newFilters: GraphFilter[] = [];
    if (isNumeric) {
      // 合并选中的数值桶为单个 range
      const selected = buckets.filter((b) => selectedBuckets.has(b.label));
      const min = Math.min(...selected.map((b) => b.min!));
      const max = Math.max(...selected.map((b) => b.max!));
      newFilters.push({ field: prop, op: 'range', value: { min, max } });
    } else {
      // 枚举型：每个选中值一个 exactMatch（简化为 range 不可用时多 filter）
      for (const label of selectedBuckets) {
        newFilters.push({ field: prop, op: 'exactMatch', value: label });
      }
    }
    const merged = [...filters.filter((f) => f.field !== prop), ...newFilters];
    setFilters(merged);
    setSelectedBuckets(new Set());
    onApplyFilter(merged);
  };

  const removeFilter = (idx: number) => {
    const next = filters.filter((_, i) => i !== idx);
    setFilters(next);
    onApplyFilter(next);
  };

  if (explore.nodes.size === 0) {
    return <div className="p-3 text-xs text-slate-400">加载对象后可查看属性分布</div>;
  }

  return (
    <div className="space-y-3 p-3 text-xs">
      {/* 已应用筛选 chip */}
      {filters.length > 0 && (
        <div className="flex flex-wrap gap-1">
          {filters.map((f, i) => (
            <span
              key={i}
              className="inline-flex items-center gap-1 rounded bg-blue-50 px-1.5 py-0.5 text-blue-700"
            >
              {f.field} {f.op}
              <button onClick={() => removeFilter(i)} className="hover:text-blue-900">×</button>
            </span>
          ))}
        </div>
      )}

      {/* 属性选择 */}
      <div>
        <select
          value={prop}
          onChange={(e) => { setProp(e.target.value); setSelectedBuckets(new Set()); }}
          className="w-full rounded border border-slate-300 px-2 py-1"
        >
          <option value="">选择属性查看分布…</option>
          {propNames.map((p) => (
            <option key={p} value={p}>{p}</option>
          ))}
        </select>
      </div>

      {/* 直方图 */}
      {buckets.length > 0 && (
        <div className="space-y-1">
          {buckets.map((b) => {
            const selected = selectedBuckets.has(b.label);
            return (
              <button
                key={b.label}
                onClick={() => toggleBucket(b.label)}
                className={`flex w-full items-center gap-2 rounded px-1 py-0.5 text-left hover:bg-slate-50 ${
                  selected ? 'bg-blue-50 ring-1 ring-blue-300' : ''
                }`}
              >
                <span className="w-24 shrink-0 truncate font-mono text-slate-500" title={b.label}>{b.label}</span>
                <div className="relative h-4 flex-1 rounded bg-slate-100">
                  <div
                    className={`h-full rounded ${selected ? 'bg-blue-500' : 'bg-slate-400'}`}
                    style={{ width: `${(b.count / maxCount) * 100}%` }}
                  />
                </div>
                <span className="w-8 shrink-0 text-right tabular-nums text-slate-600">{b.count}</span>
              </button>
            );
          })}
        </div>
      )}

      {/* 操作 */}
      {selectedBuckets.size > 0 && (
        <button
          onClick={applyFilterTo}
          className="w-full rounded bg-blue-600 px-2 py-1 text-white hover:bg-blue-700"
        >
          Filter to（保留 {selectedBuckets.size} 个桶）
        </button>
      )}
      {prop && buckets.length === 0 && (
        <div className="text-slate-400">该属性无数据</div>
      )}
    </div>
  );
}

/** helper：把 filters 塞进 ObjectSetIR（供 GraphExplorePage 调 loadStartSet）。 */
export function buildIRWithFilters(
  baseIR: ObjectSetIR,
  filters: GraphFilter[],
): ObjectSetIR {
  return { ...baseIR, filters: filters.length > 0 ? filters : undefined };
}
