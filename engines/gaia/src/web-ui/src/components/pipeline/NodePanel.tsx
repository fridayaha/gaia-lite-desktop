/**
 * NodePanel — 左侧算子面板。
 *
 * 将可拖拽的节点类型按类别分组展示（数据源/转换/输出/质量）。
 * 用户拖拽到画布即创建对应节点。
 */
import { useState } from 'react';
import type { DragEvent } from 'react';
import { getNodeDefsByCategory, NODE_COLORS, NODE_CATEGORY_LABELS } from './NodeRegistry';

const CATEGORIES = ['source', 'transform', 'sink', 'quality'] as const;

const OPERATOR_ICONS: Record<string, string> = {
  Source: '📡',
  Filter: '🔍',
  Select: '📋',
  Rename: '✏️',
  TypeCast: '🔄',
  Join: '🔗',
  Aggregate: '📊',
  Union: '🔀',
  Expression: '🧮',
  Deduplicate: '🧹',
  Sort: '↕️',
  Sink: '💾',
  'QualityCheck-NotNull': '❌',
  'QualityCheck-Unique': '🔑',
  'QualityCheck-Range': '📏',
  'QualityCheck-Regex': '📝',
};

interface NodePanelProps {
  onDragStart: (event: DragEvent, nodeType: string) => void;
  /** 是否把面板折叠成窄条。 */
  collapsed?: boolean;
  onToggleCollapse?: () => void;
}

export function NodePanel({ onDragStart, collapsed = false, onToggleCollapse }: NodePanelProps) {
  const [expandedCategories, setExpandedCategories] = useState<Record<string, boolean>>({
    source: true,
    transform: true,
    sink: true,
  });

  const toggleCategory = (cat: string) => {
    setExpandedCategories((prev) => ({ ...prev, [cat]: !prev[cat] }));
  };

  if (collapsed) {
    return (
      <div className="flex flex-col items-center gap-2 border-r border-slate-200 bg-slate-50 py-2">
        {CATEGORIES.map((cat) => (
          <button
            key={cat}
            onClick={onToggleCollapse}
            className="flex h-8 w-8 items-center justify-center rounded text-sm hover:bg-slate-200"
            title={NODE_CATEGORY_LABELS[cat]}
            style={{ color: NODE_COLORS[cat] }}
          >
            {cat === 'source' ? '📡' : cat === 'transform' ? '⚙️' : cat === 'sink' ? '💾' : '✅'}
          </button>
        ))}
        <div className="flex-1" />
        <button
          onClick={onToggleCollapse}
          className="rounded p-1 text-xs text-slate-400 hover:text-slate-600"
          title="展开面板"
        >
          »
        </button>
      </div>
    );
  }

  return (
    <div className="flex w-56 flex-col border-r border-slate-200 bg-slate-50">
      {/* 面板头部 */}
      <div className="flex items-center justify-between border-b border-slate-200 px-3 py-2">
        <span className="text-xs font-semibold uppercase tracking-wider text-slate-500">算子</span>
        <button
          onClick={onToggleCollapse}
          className="rounded p-0.5 text-xs text-slate-400 hover:text-slate-600"
          title="折叠面板"
        >
          «
        </button>
      </div>
      {/* 算子列表 */}
      <div className="flex-1 overflow-y-auto">
        {CATEGORIES.map((cat) => {
          const defs = getNodeDefsByCategory(cat);
          const isExpanded = expandedCategories[cat] ?? true;
          return (
            <div key={cat} className="border-b border-slate-200">
              <button
                className="flex w-full items-center gap-2 px-3 py-2 text-left text-xs font-semibold text-slate-600 hover:bg-slate-100"
                onClick={() => toggleCategory(cat)}
              >
                <span
                  className="inline-block h-2 w-2 rounded-full"
                  style={{ backgroundColor: NODE_COLORS[cat] }}
                />
                {NODE_CATEGORY_LABELS[cat]}
                <span className="text-[10px] text-slate-400">({defs.length})</span>
                <span className="ml-auto text-[10px] text-slate-400">
                  {isExpanded ? '▼' : '▶'}
                </span>
              </button>
              {isExpanded && (
                <div className="space-y-0.5 px-2 pb-2">
                  {defs.map((def) => (
                    <div
                      key={def.panelKey}
                      className="group cursor-grab rounded px-2 py-1.5 text-xs hover:bg-white active:cursor-grabbing"
                      draggable
                      onDragStart={(e) => onDragStart(e, def.panelKey)}
                      title={def.description}
                    >
                      <div className="flex items-center gap-2">
                        <span className="text-sm">{OPERATOR_ICONS[def.panelKey] ?? '⚙️'}</span>
                        <div className="min-w-0 flex-1">
                          <div className="truncate font-medium text-slate-700">
                            {def.displayName}
                          </div>
                          <div className="truncate text-[10px] text-slate-400">
                            {def.description}
                          </div>
                        </div>
                      </div>
                    </div>
                  ))}
                </div>
              )}
            </div>
          );
        })}
      </div>
    </div>
  );
}
