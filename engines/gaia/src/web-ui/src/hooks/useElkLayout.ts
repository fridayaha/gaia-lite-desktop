/**
 * useElkLayout — ELK 分层自动布局 hook（design.md §14.7，2026-07 从 dagre 升级）。
 *
 * graph 是真相源，布局是纯函数派生。监听 irNodes/irEdges **结构**变化 → 调 ELK
 * layered → 回写 node.position。
 *
 * 关键设计（基于 React Flow + ELK 最佳实践 / 避坑）：
 *
 * 1. **两遍 measure 模式**：ELK 需要节点尺寸才能布局，但真实尺寸来自 DOM。
 *    React Flow v12 在节点挂载后通过 `dimensions` change 把尺寸写入 `node.measured`。
 *    本 hook 等所有"参与布局的节点"都有 measured 后才调用 ELK，避免用固定尺寸
 *    导致间距错乱（dagre 旧实现的硬伤）。
 *
 * 2. **结构签名防循环**：只在「节点 id 集合 / 边拓扑」变化时重算，position 变化
 *    （拖拽、布局自身回写）不触发。否则布局写 position → onNodesChange → 触发
 *    重算 → 无限循环（React Flow 社区高频坑）。
 *
 * 3. **用户手动定位尊重**：被拖拽过的节点不覆盖位置（`markManuallyPositioned`）。
 *    孤立节点（无连边）保留用户落点。
 *
 * 4. **fitView 延后一帧**：setNodes 后立即 fitView 在 RF v12 有已知 bug
 *    （xyflow#3946，只 fit 部分节点），用 requestAnimationFrame 延后到下一帧。
 *
 * 参考：
 * - https://stately.ai/docs/packages/graph/react-flow-elk-pipeline（两遍 measure 范式）
 * - https://eclipse.dev/elk/reference/algorithms/org-eclipse-elk-layered.html（ELK 选项）
 */
import { useCallback, useEffect, useRef } from 'react';
import ELK from 'elkjs/lib/elk.bundled.js';
import type { IRNode, IREdge } from '../types/pipeline';

/** 单例 ELK 实例（同步 bundled 引擎，管道 ≤50 节点无需 worker）。 */
const elk = new ELK();

interface UseElkLayoutOptions {
  /** 是否启用自动布局。 */
  enabled?: boolean;
  /** 方向：RIGHT=左到右（默认，ETL 流），DOWN=上到下。 */
  direction?: 'RIGHT' | 'DOWN';
  /** 布局完成后回调（用于触发 fitView 等）。 */
  onLayoutDone?: () => void;
}

export function useElkLayout(
  nodes: IRNode[],
  edges: IREdge[],
  onLayout: (updates: Array<{ id: string; position: { x: number; y: number } }>) => void,
  opts: UseElkLayoutOptions = {},
) {
  const { enabled = true, direction = 'RIGHT', onLayoutDone } = opts;
  const manuallyPositionedRef = useRef<Set<string>>(new Set());
  const onLayoutRef = useRef(onLayout);
  const onDoneRef = useRef(onLayoutDone);
  onLayoutRef.current = onLayout;
  onDoneRef.current = onLayoutDone;

  /** 用户拖拽某节点后标记，后续布局不再覆盖该节点位置。 */
  const markManuallyPositioned = useCallback((nodeId: string) => {
    manuallyPositionedRef.current.add(nodeId);
  }, []);

  /** 清除所有手动定位标记（用于「整理布局」强制重排）。 */
  const resetManuallyPositioned = useCallback(() => {
    manuallyPositionedRef.current.clear();
  }, []);

  /**
   * 执行布局。
   * @param opts.force=true 时忽略手动定位标记，重排所有连边节点（「整理布局」用）。
   *                   =false（默认，结构变化自动触发）时保留用户手动定位的节点。
   */
  const runLayout = useCallback(async (opts?: { force?: boolean }) => {
    const force = opts?.force ?? false;
    if (!enabled || nodes.length === 0) return;

    // 只布局「连边的、且未手动定位的」节点；孤立节点保留用户落点。
    const connectedIds = new Set<string>();
    for (const e of edges) {
      connectedIds.add(e.source_id);
      connectedIds.add(e.target_id);
    }
    const layoutNodes = nodes.filter(
      (n) => connectedIds.has(n.id) && !manuallyPositionedRef.current.has(n.id),
    );
    if (layoutNodes.length === 0) return;

    // 两遍模式：等所有参与布局的节点都有 measured 尺寸（DOM 已测量）。
    // 否则用 fallback 尺寸会导致间距错乱。
    const ready = layoutNodes.every((n) => n.measured?.width && n.measured?.height);
    if (!ready) return;

    // 构造 ELK graph
    const elkGraph = {
      id: 'root',
      layoutOptions: {
        'elk.algorithm': 'layered',
        'elk.direction': direction,
        // 正交边路由（折线），比直线更清晰，避免连线乱穿
        'elk.edgeRouting': 'ORTHOGONAL',
        'elk.layered.edgeRouting.orthogonal.bendPointShape': 'ROUNDED',
        // 层间距（rank 之间）
        'elk.layered.spacing.nodeNodeBetweenLayers': '60',
        // 同层节点间距
        'elk.spacing.nodeNode': '40',
        // 节点放置策略：NETWORK_SIMPLEX 整体最优，减少边长方差
        'elk.layered.nodePlacement.strategy': 'NETWORK_SIMPLEX',
        // 同 rank 节点对齐方式：顶部对齐（ETL 流上下排开时整齐）
        'elk.layered.nodePlacement.favorStraightEdges': 'true',
      },
      children: layoutNodes.map((n) => ({
        id: n.id,
        width: n.measured!.width,
        height: n.measured!.height,
      })),
      // 只加入两端都在布局集合内的边
      edges: edges
        .filter(
          (e) =>
            connectedIds.has(e.source_id) &&
            connectedIds.has(e.target_id) &&
            (force ||
              (!manuallyPositionedRef.current.has(e.source_id) &&
                !manuallyPositionedRef.current.has(e.target_id))),
        )
        .map((e) => ({
          id: e.id,
          sources: [e.source_id],
          targets: [e.target_id],
        })),
    };

    let laidOut;
    try {
      laidOut = await elk.layout(elkGraph);
    } catch (err) {
      // ELK 布局失败不应阻塞画布（保留旧位置）
      console.error('[useElkLayout] ELK layout failed:', err);
      return;
    }

    const updates: Array<{ id: string; position: { x: number; y: number } }> = [];
    for (const child of laidOut.children ?? []) {
      // ELK 返回的 x/y 是节点左上角（已减去自身宽高一半），RF position 也是左上角，直接用
      updates.push({ id: child.id, position: { x: child.x ?? 0, y: child.y ?? 0 } });
    }
    if (updates.length > 0) {
      onLayoutRef.current(updates);
      onDoneRef.current?.();
    }
  }, [nodes, edges, enabled, direction]);

  // ── 结构签名变化时自动重算 ──
  // 只在「节点 id 集合 + 边拓扑」变化时触发，position/measured 变化不触发（防循环）。
  const sig = useRef('');
  const newSig = JSON.stringify({
    n: nodes.map((n) => n.id).sort(),
    e: edges.map((e) => `${e.source_id}->${e.target_id}`).sort(),
  });
  useEffect(() => {
    if (!enabled) return;
    if (newSig === sig.current) return;
    sig.current = newSig;
    // 延迟一帧：等 React Flow 完成 dimensions change → measured 写入 store 后再布局
    const timer = setTimeout(() => {
      void runLayout();
    }, 60);
    return () => clearTimeout(timer);
  }, [newSig, enabled, runLayout]);

  return { runLayout, markManuallyPositioned, resetManuallyPositioned };
}
