/**
 * LayersPanel — 图层样式面板（graph-reasoning-frontend-design.md §3.4 Layers tab）。
 *
 * - 着色：按 ObjectType（默认）/ 按属性值（自动分配调色板）
 * - 大小：固定 / 按度数 / 按属性值
 * - 图例：属性着色时显示 值→颜色 映射
 * - 持久化：localStorage（二期不入库）
 */
import type { useGraphExplore, LayerStyle } from '../hooks/useGraphExplore';

interface LayersPanelProps {
  explore: ReturnType<typeof useGraphExplore>;
}

export function LayersPanel({ explore }: LayersPanelProps) {
  const ls = explore.layerStyle;

  const update = (patch: Partial<LayerStyle>) => {
    explore.setLayerStyle({ ...ls, ...patch });
  };

  // 收集画布节点上所有可用属性名（取首个节点的 props）
  const sampleNode = explore.nodes.values().next().value;
  const propNames = sampleNode ? Object.keys(sampleNode.props) : [];

  const colorMapEntries = ls.colorMap ? Object.entries(ls.colorMap) : [];

  return (
    <div className="space-y-4 p-3 text-xs">
      {/* 着色 */}
      <div>
        <h4 className="mb-1.5 font-semibold text-slate-600">着色</h4>
        <div className="flex gap-2">
          <label className="flex items-center gap-1">
            <input
              type="radio"
              checked={ls.colorBy === 'type'}
              onChange={() => update({ colorBy: 'type', colorMap: undefined })}
            />
            按类型
          </label>
          <label className="flex items-center gap-1">
            <input
              type="radio"
              checked={ls.colorBy === 'property'}
              onChange={() => update({ colorBy: 'property' })}
              disabled={propNames.length === 0}
            />
            按属性
          </label>
        </div>
        {ls.colorBy === 'property' && (
          <select
            value={ls.colorProp ?? ''}
            onChange={(e) => update({ colorProp: e.target.value, colorMap: undefined })}
            className="mt-1.5 w-full rounded border border-slate-300 px-2 py-1"
            disabled={propNames.length === 0}
          >
            <option value="">选择属性…</option>
            {propNames.map((p) => (
              <option key={p} value={p}>{p}</option>
            ))}
          </select>
        )}
        {/* 图例 */}
        {ls.colorBy === 'property' && ls.colorProp && colorMapEntries.length > 0 && (
          <div className="mt-2 space-y-1">
            <div className="text-slate-400">图例</div>
            {colorMapEntries.map(([val, color]) => (
              <div key={val} className="flex items-center gap-2">
                <span className="h-3 w-3 rounded-full" style={{ backgroundColor: color }} />
                <span className="text-slate-600">{val}</span>
              </div>
            ))}
          </div>
        )}
      </div>

      {/* 大小 */}
      <div>
        <h4 className="mb-1.5 font-semibold text-slate-600">节点大小</h4>
        <div className="flex flex-wrap gap-2">
          <label className="flex items-center gap-1">
            <input
              type="radio"
              checked={ls.sizeBy === 'fixed'}
              onChange={() => update({ sizeBy: 'fixed' })}
            />
            固定
          </label>
          <label className="flex items-center gap-1">
            <input
              type="radio"
              checked={ls.sizeBy === 'degree'}
              onChange={() => update({ sizeBy: 'degree' })}
            />
            按度数
          </label>
          <label className="flex items-center gap-1">
            <input
              type="radio"
              checked={ls.sizeBy === 'property'}
              onChange={() => update({ sizeBy: 'property' })}
              disabled={propNames.length === 0}
            />
            按属性
          </label>
        </div>
        {ls.sizeBy === 'property' && (
          <select
            value={ls.sizeProp ?? ''}
            onChange={(e) => update({ sizeProp: e.target.value })}
            className="mt-1.5 w-full rounded border border-slate-300 px-2 py-1"
            disabled={propNames.length === 0}
          >
            <option value="">选择数值属性…</option>
            {propNames.map((p) => (
              <option key={p} value={p}>{p}</option>
            ))}
          </select>
        )}
      </div>

      {/* 重置 */}
      <button
        onClick={() => explore.setLayerStyle({ colorBy: 'type', sizeBy: 'fixed' })}
        className="rounded border border-slate-300 px-2 py-1 text-slate-500 hover:bg-slate-50"
      >
        重置样式
      </button>

      {explore.nodes.size === 0 && (
        <div className="text-slate-400">加载对象后可配置图层样式</div>
      )}
    </div>
  );
}
