/**
 * DeletableEdge — 可删除的连线（自定义 Edge）。
 *
 * 设计取舍（基于 React Flow v12 最佳实践 + 误删防护）：
 *
 * 1. 删除按钮「选中后显示」而非「hover 显示」
 *    - 必须先点击选中边，删除按钮才出现（两步操作，大幅降低误删）
 *    - React Flow 官方 EdgeToolbar 范式：选中 → 显示工具
 *    - hover 仅高亮连线本身，给「可点选」的视觉提示，不弹按钮
 *
 * 2. 交互路径加宽（不可见）
 *    - 用一条 20px 宽的透明 stroke 覆盖在可见连线上，让细线也容易点选
 *    - 仅影响「点选边」的命中，不放大删除按钮的点击区
 *
 * 3. 点击区严格限定在按钮本身
 *    - 按钮无透明 padding 扩张，只有显式点中按钮才触发删除
 *
 * 4. 删除走 setEdges → onEdgesChange(remove)，store 已带 undo 快照
 */
import { memo } from 'react';
import {
  BaseEdge,
  EdgeLabelRenderer,
  getSmoothStepPath,
  type EdgeProps,
  useReactFlow,
} from '@xyflow/react';

function DeletableEdgeComponent({
  id,
  sourceX,
  sourceY,
  targetX,
  targetY,
  sourcePosition,
  targetPosition,
  selected,
}: EdgeProps) {
  const { setEdges } = useReactFlow();

  const [edgePath, labelX, labelY] = getSmoothStepPath({
    sourceX,
    sourceY,
    sourcePosition,
    targetX,
    targetY,
    targetPosition,
  });

  const handleDelete = (e: React.MouseEvent) => {
    e.stopPropagation();
    // setEdges 触发 onEdgesChange(remove)，store 已带 undo 快照
    setEdges((edges) => edges.filter((edge) => edge.id !== id));
  };

  const strokeColor = selected ? '#ef4444' : '#94a3b8';
  const strokeWidth = selected ? 3 : 2;

  return (
    <>
      {/* 可见连线；interactionWidth 提供 20px 不可见交互区让细线易点选 */}
      <BaseEdge
        id={id}
        path={edgePath}
        interactionWidth={20}
        style={{ stroke: strokeColor, strokeWidth }}
      />
      {/* 删除按钮：仅选中时显示 */}
      <EdgeLabelRenderer>
        {selected && (
          <div
            style={{
              position: 'absolute',
              transform: `translate(-50%, -50%) translate(${labelX}px, ${labelY}px)`,
              pointerEvents: 'all',
            }}
            className="nodrag nopan"
          >
            <button
              type="button"
              onClick={handleDelete}
              title="删除连线"
              aria-label="删除连线"
              className="flex h-6 w-6 items-center justify-center rounded-full border-2 border-red-500 bg-white text-red-500 shadow-md transition hover:bg-red-500 hover:text-white"
            >
              <svg width="12" height="12" viewBox="0 0 12 12" fill="none" aria-hidden="true">
                <path
                  d="M3 3L9 9M9 3L3 9"
                  stroke="currentColor"
                  strokeWidth="2"
                  strokeLinecap="round"
                />
              </svg>
            </button>
          </div>
        )}
      </EdgeLabelRenderer>
    </>
  );
}

export const DeletableEdge = memo(DeletableEdgeComponent);
