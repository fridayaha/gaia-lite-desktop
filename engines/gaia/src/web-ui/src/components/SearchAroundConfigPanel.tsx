/**
 * SearchAroundConfigPanel — 多步 Search Around 配置面板（design-v2 §1.1）。
 *
 * 可视化构建嵌套 ObjectSet IR：起始集 → Link1 → Link2 → ...
 * 每跳配关系类型/方向/跳数/属性过滤，预览命中数量防星爆。
 */
import { useState } from 'react';
import type { useSearchAroundConfig } from '../hooks/useSearchAroundConfig';
import type { LinkTypeDef, GraphFilter, ObjectSetIR } from '../types';

interface SearchAroundConfigPanelProps {
  ontology: string;
  linkTypes: LinkTypeDef[];
  config: ReturnType<typeof useSearchAroundConfig>;
  selectedVids: string[];
  onExecute: (ir: ObjectSetIR) => void;
}

const FILTER_OPS = ['exactMatch', 'range', 'contains', 'isNull', 'isNotNull'] as const;

export function SearchAroundConfigPanel({
  ontology,
  linkTypes,
  config,
  selectedVids,
  onExecute,
}: SearchAroundConfigPanelProps) {
  const [filterFields, setFilterFields] = useState<Record<string, string>>({});
  const [filterValues, setFilterValues] = useState<Record<string, string>>({});
  const [filterOps, setFilterOps] = useState<Record<string, string>>({});

  // 起始集 = 当前选中节点（或手动输入）
  const startLabel =
    config.startVids.length > 0
      ? `${config.startVids.length} 个对象`
      : selectedVids.length > 0
        ? `${selectedVids.length} 个选中`
        : '未选';

  const handleSetStart = () => {
    if (selectedVids.length > 0) {
      config.setStart(selectedVids);
    }
  };

  const addFilter = (stepId: string) => {
    const field = filterFields[stepId];
    const op = filterOps[stepId] || 'exactMatch';
    const value = filterValues[stepId];
    if (!field) return;
    const step = config.steps.find((s) => s.id === stepId);
    if (!step) return;
    const filter: GraphFilter = {
      field,
      op: op as GraphFilter['op'],
      value: op === 'isNull' || op === 'isNotNull' ? undefined : value,
    };
    config.updateStep(stepId, { filters: [...step.filters, filter] });
    setFilterFields((p) => ({ ...p, [stepId]: '' }));
    setFilterValues((p) => ({ ...p, [stepId]: '' }));
  };

  const removeFilter = (stepId: string, idx: number) => {
    const step = config.steps.find((s) => s.id === stepId);
    if (!step) return;
    config.updateStep(stepId, { filters: step.filters.filter((_, i) => i !== idx) });
  };

  const handleExecute = () => {
    const ir = config.buildIR();
    if (ir) onExecute(ir);
  };

  const canExecute = config.buildIR() !== null;

  return (
    <div className="space-y-3 p-3 text-xs">
      {/* 起始对象集 */}
      <div className="rounded border border-slate-200 p-2">
        <div className="mb-1 flex items-center justify-between">
          <span className="font-semibold text-slate-600">起始对象集</span>
          <span className="text-slate-400">{startLabel}</span>
        </div>
        <button
          onClick={handleSetStart}
          disabled={selectedVids.length === 0}
          className="w-full rounded border border-slate-300 px-2 py-1 text-slate-600 hover:bg-slate-50 disabled:opacity-50"
        >
          {config.startVids.length > 0 ? '重置为当前选中' : '设为当前选中节点'}
        </button>
        {config.startVids.length > 0 && (
          <div className="mt-1 flex flex-wrap gap-1">
            {config.startVids.slice(0, 5).map((v) => (
              <span key={v} className="rounded bg-slate-100 px-1 py-0.5 font-mono text-[10px]">
                {v}
              </span>
            ))}
            {config.startVids.length > 5 && (
              <span className="text-[10px] text-slate-400">…+{config.startVids.length - 5}</span>
            )}
          </div>
        )}
      </div>

      {/* 链式跳配置 */}
      {config.steps.map((step, i) => (
        <div key={step.id} className="rounded border border-blue-200 bg-blue-50/30 p-2">
          <div className="mb-1.5 flex items-center justify-between">
            <span className="font-semibold text-blue-700">Link {i + 1}</span>
            <button
              onClick={() => config.removeStep(step.id)}
              className="text-slate-400 hover:text-red-500"
              title="移除此跳及之后"
            >
              ✕
            </button>
          </div>

          {/* 关系类型 + 方向 */}
          <div className="mb-1.5 grid grid-cols-2 gap-1.5">
            <select
              value={step.linkType}
              onChange={(e) => config.updateStep(step.id, { linkType: e.target.value })}
              className="rounded border border-slate-300 px-1.5 py-0.5"
            >
              <option value="">关系类型…</option>
              {linkTypes.map((lt) => (
                <option key={lt.id} value={lt.api_name}>
                  {lt.api_name}
                </option>
              ))}
            </select>
            <select
              value={step.direction}
              onChange={(e) =>
                config.updateStep(step.id, {
                  direction: e.target.value as 'forward' | 'reverse',
                })
              }
              className="rounded border border-slate-300 px-1.5 py-0.5"
            >
              <option value="forward">下游（正向）</option>
              <option value="reverse">上游（反向）</option>
            </select>
          </div>

          {/* 跳数 */}
          <div className="mb-1.5 flex items-center gap-2">
            <span className="text-slate-400">跳数</span>
            <input
              type="range"
              min={1}
              max={5}
              value={step.maxHops}
              onChange={(e) => config.updateStep(step.id, { maxHops: Number(e.target.value) })}
              className="flex-1"
            />
            <span className="w-8 text-center font-mono text-slate-600">{step.maxHops}</span>
          </div>

          {/* 属性过滤 */}
          {step.filters.map((f, idx) => (
            <div
              key={idx}
              className="mb-1 flex items-center gap-1 rounded bg-white px-1.5 py-0.5"
            >
              <span className="font-mono text-[10px] text-slate-500">
                {f.field} {f.op} {String(f.value ?? '')}
              </span>
              <button
                onClick={() => removeFilter(step.id, idx)}
                className="ml-auto text-slate-300 hover:text-red-500"
              >
                ×
              </button>
            </div>
          ))}
          <div className="flex gap-1">
            <input
              value={filterFields[step.id] ?? ''}
              onChange={(e) => setFilterFields((p) => ({ ...p, [step.id]: e.target.value }))}
              placeholder="属性名"
              className="w-20 rounded border border-slate-300 px-1 py-0.5 text-[10px]"
            />
            <select
              value={filterOps[step.id] ?? 'exactMatch'}
              onChange={(e) => setFilterOps((p) => ({ ...p, [step.id]: e.target.value }))}
              className="rounded border border-slate-300 px-1 py-0.5 text-[10px]"
            >
              {FILTER_OPS.map((op) => (
                <option key={op} value={op}>
                  {op}
                </option>
              ))}
            </select>
            <input
              value={filterValues[step.id] ?? ''}
              onChange={(e) => setFilterValues((p) => ({ ...p, [step.id]: e.target.value }))}
              placeholder="值"
              className="w-16 rounded border border-slate-300 px-1 py-0.5 text-[10px]"
            />
            <button
              onClick={() => addFilter(step.id)}
              className="rounded bg-slate-200 px-1.5 py-0.5 text-[10px] hover:bg-slate-300"
            >
              + 过滤
            </button>
          </div>

          {/* 预览 */}
          <div className="mt-1.5 flex items-center gap-2">
            <button
              onClick={() => config.previewStep(ontology, step.id)}
              disabled={!step.linkType || config.startVids.length === 0 || step.previewing}
              className="rounded border border-slate-300 px-2 py-0.5 text-[10px] hover:bg-slate-50 disabled:opacity-50"
            >
              {step.previewing ? '预览中…' : '预览命中'}
            </button>
            {step.previewCount !== undefined && (
              <span
                className={`text-[10px] ${
                  step.previewCount > 50 ? 'text-amber-600' : 'text-slate-500'
                }`}
              >
                {step.previewCount > 50
                  ? `⚠ ${step.previewCount} 个（建议加过滤）`
                  : `${step.previewCount} 个`}
              </span>
            )}
          </div>
        </div>
      ))}

      {/* 添加下一跳 */}
      <button
        onClick={config.addStep}
        className="w-full rounded border border-dashed border-slate-300 py-1 text-slate-400 hover:border-blue-400 hover:text-blue-500"
      >
        + 添加下一跳
      </button>

      {/* 执行 */}
      <div className="flex gap-2">
        <button
          onClick={handleExecute}
          disabled={!canExecute}
          className="flex-1 rounded bg-blue-600 px-2 py-1 text-white hover:bg-blue-700 disabled:opacity-50"
        >
          全部展开到画布
        </button>
        <button
          onClick={config.reset}
          className="rounded border border-slate-300 px-2 py-1 text-slate-500 hover:bg-slate-50"
        >
          重置
        </button>
      </div>

      {config.startVids.length === 0 && (
        <div className="text-center text-[10px] text-slate-400">
          先在画布选中节点，再设为起始集
        </div>
      )}
    </div>
  );
}
