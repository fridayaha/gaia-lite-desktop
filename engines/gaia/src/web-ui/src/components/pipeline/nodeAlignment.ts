/**
 * nodeAlignment — 多选节点的对齐与分布几何计算（纯函数）。
 *
 * 对标 Dify / Adobe Substance / Figma 的对齐工具：
 * - 6 种对齐：左 / 水平居中 / 右 / 顶 / 垂直居中 / 底
 * - 2 种分布：水平等距 / 垂直等距
 *
 * 节点尺寸来自 React Flow 的 measured（DOM 测量值），无 measured 时用 fallback。
 * 返回 { id, position } 增量更新，调用方负责写回 store。
 */
import type { IRNode } from '../../types/pipeline';

const FALLBACK_W = 200;
const FALLBACK_H = 70;

interface Box {
  id: string;
  x: number;
  y: number;
  w: number;
  h: number;
}

function toBox(n: IRNode): Box {
  return {
    id: n.id,
    x: n.position.x,
    y: n.position.y,
    w: n.measured?.width ?? FALLBACK_W,
    h: n.measured?.height ?? FALLBACK_H,
  };
}

export type AlignMode = 'left' | 'centerH' | 'right' | 'top' | 'centerV' | 'bottom';
export type DistributeMode = 'horizontal' | 'vertical';

/**
 * 对齐选中节点。至少 2 个节点才有意义。
 * - left:    所有节点 x 对齐到最小 x
 * - centerH: 所有节点水平中心对齐到选中集合的水平中心
 * - right:   所有节点右边对齐到最大 right
 * - top:     所有节点 y 对齐到最小 y
 * - centerV: 所有节点垂直中心对齐到选中集合的垂直中心
 * - bottom:  所有节点底边对齐到最大 bottom
 */
export function alignNodes(
  nodes: IRNode[],
  mode: AlignMode,
): Array<{ id: string; position: { x: number; y: number } }> {
  if (nodes.length < 2) return [];
  const boxes = nodes.map(toBox);

  const minX = Math.min(...boxes.map((b) => b.x));
  const maxX = Math.max(...boxes.map((b) => b.x + b.w));
  const minY = Math.min(...boxes.map((b) => b.y));
  const maxY = Math.max(...boxes.map((b) => b.y + b.h));
  const centerHX = (minX + maxX) / 2;
  const centerVY = (minY + maxY) / 2;

  return boxes.map((b) => {
    let { x, y } = b;
    switch (mode) {
      case 'left':
        x = minX;
        break;
      case 'centerH':
        x = centerHX - b.w / 2;
        break;
      case 'right':
        x = maxX - b.w;
        break;
      case 'top':
        y = minY;
        break;
      case 'centerV':
        y = centerVY - b.h / 2;
        break;
      case 'bottom':
        y = maxY - b.h;
        break;
    }
    return { id: b.id, position: { x, y } };
  });
}

/**
 * 分布选中节点（水平/垂直等距）。至少 3 个节点才有意义。
 * - horizontal: 按 x 排序，在最左和最右之间均匀分布，保持原顺序
 * - vertical:   按 y 排序，在最上和最下之间均匀分布，保持原顺序
 */
export function distributeNodes(
  nodes: IRNode[],
  mode: DistributeMode,
): Array<{ id: string; position: { x: number; y: number } }> {
  if (nodes.length < 3) return [];
  const boxes = nodes.map(toBox);

  if (mode === 'horizontal') {
    const sorted = [...boxes].sort((a, b) => a.x - b.x);
    const first = sorted[0];
    const last = sorted[sorted.length - 1];
    const totalSpan = last.x - first.x;
    const step = totalSpan / (sorted.length - 1);
    return sorted.map((b, i) => ({
      id: b.id,
      position: { x: first.x + step * i, y: b.y },
    }));
  } else {
    const sorted = [...boxes].sort((a, b) => a.y - b.y);
    const first = sorted[0];
    const last = sorted[sorted.length - 1];
    const totalSpan = last.y - first.y;
    const step = totalSpan / (sorted.length - 1);
    return sorted.map((b, i) => ({
      id: b.id,
      position: { x: b.x, y: first.y + step * i },
    }));
  }
}
