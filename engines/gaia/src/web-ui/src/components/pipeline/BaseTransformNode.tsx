/**
 * BaseTransformNode — 转换节点（画布上的 Transform / QualityCheck）。
 *
 * 样式：绿色边框，2D 菱形图标，显示操作符名称，输入/输出端口。
 */
import { memo } from 'react';
import { Handle, Position, type Node, type NodeProps } from '@xyflow/react';
import type { PipelineNodeData } from '../../types/pipeline';
import { NODE_COLORS, getNodeDef } from './NodeRegistry';

/** 渲染配置摘要区（主信息深色加粗，次信息浅色）。 */
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

const OPERATOR_ICONS: Record<string, string> = {
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
  NotNULLCheck: '❌',
  UniqueCheck: '🔑',
  RangeCheck: '📏',
  RegexCheck: '📝',
};

function BaseTransformNodeComponent({ data, selected }: NodeProps<Node<PipelineNodeData>>) {
  const def = getNodeDef(data.operatorType);
  const category = def?.category ?? 'transform';
  const color = NODE_COLORS[category] ?? NODE_COLORS.transform;
  const icon = OPERATOR_ICONS[data.operatorType] ?? '⚙️';
  const inputCount = def?.inputPorts ?? 1;

  return (
    <div
      className={`relative rounded-lg border-2 bg-white px-4 py-3 shadow-sm transition-shadow ${
        selected ? 'shadow-md ring-2 ring-blue-300' : ''
      }`}
      style={{ borderColor: selected ? '#3b82f6' : color }}
    >
      {/* 输入端口（单端口居中；多端口垂直均分）*/}
      {inputCount > 0 &&
        Array.from({ length: inputCount }, (_, i) => {
          // 均分定位：inputCount=1 → 50%；=2 → 25%/75%；=3 → 16.7/50/83.3
          const topPct = inputCount === 1 ? 50 : ((i + 1) / (inputCount + 1)) * 100;
          return (
            <Handle
              key={i}
              type="target"
              position={Position.Left}
              id={i === 0 ? 'default' : `input-${i}`}
              isConnectableStart={false}
              isConnectableEnd
              style={{ backgroundColor: color, top: `${topPct}%` }}
            />
          );
        })}
      {/* 节点主体 */}
      <div className="flex items-center gap-2">
        <div
          className="flex h-7 w-7 items-center justify-center rounded-lg text-xs text-white"
          style={{ backgroundColor: color }}
        >
          {icon}
        </div>
        <div className="min-w-0 flex-1">
          <div className="truncate text-sm font-semibold text-slate-800">{data.label}</div>
          <div className="text-[10px] text-slate-500">{def?.displayName ?? data.operatorType}</div>
        </div>
        {data.validationStatus === 'error' && (
          <span className="text-xs text-red-500" title={data.validationMessages.join(', ')}>
            ⚠
          </span>
        )}
        {data.validationStatus === 'warning' && (
          <span className="text-xs text-amber-500" title={data.validationMessages.join(', ')}>
            ⚠
          </span>
        )}
        {data.isRunning && (
          <span className="h-2 w-2 animate-pulse rounded-full bg-green-500" title="执行中" />
        )}
      </div>
      {/* Schema 摘要 */}
      {data.outputSchemaSummary && (
        <div className="mt-1 text-[10px] text-slate-400">{data.outputSchemaSummary}</div>
      )}
      {/* 配置摘要（核心配置可视化） */}
      <ConfigSummaryLines lines={data.configSummary ?? []} />
      {/* 输出端口 */}
      <Handle
        type="source"
        position={Position.Right}
        id="default"
        isConnectableStart
        isConnectableEnd={false}
        style={{ backgroundColor: color }}
      />
    </div>
  );
}

export const BaseTransformNode = memo(BaseTransformNodeComponent);
