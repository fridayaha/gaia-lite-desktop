/**
 * BaseSinkNode — 输出节点（画布上的 Sink）。
 *
 * 样式：橙色边框，显示目标 Dataset 名称，仅输入端口。
 */
import { memo } from 'react';
import { Handle, Position, type Node, type NodeProps } from '@xyflow/react';
import type { PipelineNodeData } from '../../types/pipeline';
import { NODE_COLORS } from './NodeRegistry';

/** 渲染配置摘要区（主信息深色，次信息浅色）。 */
function ConfigSummaryLines({ lines }: { lines: PipelineNodeData['configSummary'] }) {
  if (lines.length === 0) return null;
  return (
    <div className="mt-1.5 space-y-0.5 border-t border-slate-100 pt-1.5">
      {lines.map((line, i) => (
        <div
          key={i}
          className={`truncate font-mono text-[10px] leading-tight ${
            line.primary ? 'text-slate-600' : 'text-slate-400'
          }`}
          title={line.text}
        >
          {line.text}
        </div>
      ))}
    </div>
  );
}

function BaseSinkNodeComponent({ data, selected }: NodeProps<Node<PipelineNodeData>>) {
  const color = NODE_COLORS.sink;
  return (
    <div
      className={`relative rounded-lg border-2 bg-white px-4 py-3 shadow-sm transition-shadow ${
        selected ? 'shadow-md ring-2 ring-blue-300' : ''
      }`}
      style={{ borderColor: selected ? '#3b82f6' : color }}
    >
      {/* 输入端口 */}
      <Handle
        type="target"
        position={Position.Left}
        id="default"
        isConnectableStart={false}
        isConnectableEnd
        style={{ backgroundColor: color }}
      />
      {/* 节点主体 */}
      <div className="flex items-center gap-2">
        <div
          className="flex h-7 w-7 items-center justify-center rounded-full text-xs text-white"
          style={{ backgroundColor: color }}
        >
          💾
        </div>
        <div className="min-w-0 flex-1">
          <div className="truncate text-sm font-semibold text-slate-800">{data.label}</div>
          <div className="text-[10px] text-slate-500">输出</div>
        </div>
        {data.validationStatus === 'error' && (
          <span className="text-xs text-red-500" title={data.validationMessages.join(', ')}>
            ⚠
          </span>
        )}
        {data.isRunning && (
          <span className="h-2 w-2 animate-pulse rounded-full bg-green-500" title="执行中" />
        )}
      </div>
      {/* 配置摘要（核心配置可视化：目标数据集 + 写入模式） */}
      <ConfigSummaryLines lines={data.configSummary ?? []} />
    </div>
  );
}

export const BaseSinkNode = memo(BaseSinkNodeComponent);
