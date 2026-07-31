/**
 * SelectionToolbar — 多选节点时浮现的对齐/分布工具条。
 *
 * 对标 Dify 的 SelectionContextmenu / Figma 的多选对齐栏。
 * 当选中 ≥2 个节点时，在选中区域上方显示：
 * - 对齐：左 / 水平居中 / 右 / 顶 / 垂直居中 / 底
 * - 分布：水平等距 / 垂直等距（≥3 个节点才启用）
 * - 复制 / 删除
 *
 * 工具条定位在选中节点 bbox 的顶部中央，随选中区域移动。
 */
import type { IRNode } from '../../types/pipeline';
import { type AlignMode, type DistributeMode } from './nodeAlignment';

interface SelectionToolbarProps {
  selectedNodes: IRNode[];
  onAlign: (mode: AlignMode) => void;
  onDistribute: (mode: DistributeMode) => void;
  onDuplicate: () => void;
  onDelete: () => void;
}

interface BtnProps {
  title: string;
  onClick: () => void;
  disabled?: boolean;
  children: React.ReactNode;
}

function Btn({ title, onClick, disabled, children }: BtnProps) {
  return (
    <button
      type="button"
      title={title}
      aria-label={title}
      disabled={disabled}
      onClick={onClick}
      className="flex h-7 w-7 items-center justify-center rounded text-slate-600 hover:bg-slate-100 disabled:opacity-30 disabled:hover:bg-transparent"
    >
      {children}
    </button>
  );
}

const Sep = () => <div className="mx-0.5 h-4 w-px bg-slate-200" />;

export function SelectionToolbar({
  selectedNodes,
  onAlign,
  onDistribute,
  onDuplicate,
  onDelete,
}: SelectionToolbarProps) {
  // 计算选中区域的屏幕坐标（用于工具条定位）。
  // 这里用节点的 position + measured 近似 bbox，靠 React Flow 的 viewport transform
  // 缩放显示。简化处理：工具条作为 <Panel> 渲染在画布固定位置（顶部居中），
  // 不跟随选中区域移动，避免 viewport 变换带来的复杂坐标换算。
  const canDistribute = selectedNodes.length >= 3;

  // 静态渲染在画布顶部居中（用 Panel position="top-center" 包裹）
  return (
    <div className="pointer-events-auto flex items-center gap-0.5 rounded-lg border border-slate-200 bg-white px-1 py-0.5 shadow-lg">
      {/* 对齐组 */}
      <Btn title="左对齐" onClick={() => onAlign('left')}>
        <svg width="14" height="14" viewBox="0 0 14 14" fill="none">
          <rect x="1" y="2" width="1.5" height="10" fill="currentColor" />
          <rect x="3" y="3" width="6" height="3" rx="0.5" fill="currentColor" />
          <rect x="3" y="8" width="4" height="3" rx="0.5" fill="currentColor" />
        </svg>
      </Btn>
      <Btn title="水平居中" onClick={() => onAlign('centerH')}>
        <svg width="14" height="14" viewBox="0 0 14 14" fill="none">
          <rect x="6.25" y="1" width="1.5" height="12" fill="currentColor" />
          <rect x="3" y="3" width="8" height="3" rx="0.5" fill="currentColor" />
          <rect x="4" y="8" width="6" height="3" rx="0.5" fill="currentColor" />
        </svg>
      </Btn>
      <Btn title="右对齐" onClick={() => onAlign('right')}>
        <svg width="14" height="14" viewBox="0 0 14 14" fill="none">
          <rect x="11.5" y="2" width="1.5" height="10" fill="currentColor" />
          <rect x="5" y="3" width="6" height="3" rx="0.5" fill="currentColor" />
          <rect x="7" y="8" width="4" height="3" rx="0.5" fill="currentColor" />
        </svg>
      </Btn>
      <Sep />
      <Btn title="顶对齐" onClick={() => onAlign('top')}>
        <svg width="14" height="14" viewBox="0 0 14 14" fill="none">
          <rect x="2" y="1" width="10" height="1.5" fill="currentColor" />
          <rect x="3" y="3" width="3" height="6" rx="0.5" fill="currentColor" />
          <rect x="8" y="3" width="3" height="4" rx="0.5" fill="currentColor" />
        </svg>
      </Btn>
      <Btn title="垂直居中" onClick={() => onAlign('centerV')}>
        <svg width="14" height="14" viewBox="0 0 14 14" fill="none">
          <rect x="1" y="6.25" width="12" height="1.5" fill="currentColor" />
          <rect x="3" y="3" width="3" height="8" rx="0.5" fill="currentColor" />
          <rect x="8" y="4" width="3" height="6" rx="0.5" fill="currentColor" />
        </svg>
      </Btn>
      <Btn title="底对齐" onClick={() => onAlign('bottom')}>
        <svg width="14" height="14" viewBox="0 0 14 14" fill="none">
          <rect x="2" y="11.5" width="10" height="1.5" fill="currentColor" />
          <rect x="3" y="5" width="3" height="6" rx="0.5" fill="currentColor" />
          <rect x="8" y="7" width="3" height="4" rx="0.5" fill="currentColor" />
        </svg>
      </Btn>
      <Sep />
      {/* 分布组 */}
      <Btn
        title="水平等距分布"
        disabled={!canDistribute}
        onClick={() => onDistribute('horizontal')}
      >
        <svg width="16" height="14" viewBox="0 0 16 14" fill="none">
          <rect x="1" y="2" width="2" height="10" rx="0.5" fill="currentColor" />
          <rect x="7" y="2" width="2" height="10" rx="0.5" fill="currentColor" />
          <rect x="13" y="2" width="2" height="10" rx="0.5" fill="currentColor" />
          <rect x="0" y="0.5" width="16" height="1" fill="currentColor" opacity="0.3" />
        </svg>
      </Btn>
      <Btn
        title="垂直等距分布"
        disabled={!canDistribute}
        onClick={() => onDistribute('vertical')}
      >
        <svg width="14" height="16" viewBox="0 0 14 16" fill="none">
          <rect x="2" y="1" width="10" height="2" rx="0.5" fill="currentColor" />
          <rect x="2" y="7" width="10" height="2" rx="0.5" fill="currentColor" />
          <rect x="2" y="13" width="10" height="2" rx="0.5" fill="currentColor" />
          <rect x="0.5" y="0" width="1" height="16" fill="currentColor" opacity="0.3" />
        </svg>
      </Btn>
      <Sep />
      <Btn title="复制选中节点" onClick={onDuplicate}>
        <svg width="14" height="14" viewBox="0 0 14 14" fill="none" stroke="currentColor" strokeWidth="1.3">
          <rect x="4" y="4" width="8" height="8" rx="1" />
          <path d="M2 9V3a1 1 0 0 1 1-1h6" />
        </svg>
      </Btn>
      <Btn title="删除选中节点" onClick={onDelete}>
        <svg width="14" height="14" viewBox="0 0 14 14" fill="none" stroke="currentColor" strokeWidth="1.3">
          <path d="M3 4h8M5 4V2.5a0.5 0.5 0 0 1 0.5-0.5h3a0.5 0.5 0 0 1 0.5 0.5V4M4.5 4l0.5 8h4l0.5-8" />
        </svg>
      </Btn>
    </div>
  );
}

/** 选中节点数量提示（工具条右侧） */
export function SelectionCount({ count }: { count: number }) {
  return (
    <span className="ml-2 text-xs text-slate-500">已选 {count} 个节点</span>
  );
}
