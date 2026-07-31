/**
 * useGraphExplore — 图探索画布状态管理（graph-reasoning-frontend-design.md §9）。
 *
 * 管理画布元素（节点/边增量）、选中态、撤销栈、LOD 折叠、图层样式。
 * 视图组件（GraphCanvas/MapPanel）订阅此 hook 的状态。
 */
import { useCallback, useEffect, useRef, useState } from 'react';
import { traverseLink, queryDataFrame } from '../api/graph';
import type { GraphNode, ObjectSetIR, ReasoningResult } from '../types';
import type { CanvasSnapshot } from '../types/canvas';

/** 画布节点（GraphNode + 画布元数据）。 */
export interface CanvasNode extends GraphNode {
  /** 已展开的 link 类型（避免重复 Search Around）。 */
  expandedLinks: Set<string>;
}

/** 画布边。 */
export interface CanvasEdge {
  id: string; // `${source}:${linkType}:${target}`
  source: string;
  target: string;
  linkType: string;
  weight?: number;
}

/** 历史记录（撤销栈用）。 */
interface HistoryEntry {
  addedNodeVids: string[];
  addedEdgeIds: string[];
}

export interface LayerStyle {
  colorBy: 'type' | 'property';
  colorProp?: string;
  /** 属性值 → 颜色映射（colorBy=property 时用）。 */
  colorMap?: Record<string, string>;
  /** 节点大小依据：固定/度数/属性值。 */
  sizeBy: 'fixed' | 'degree' | 'property';
  sizeProp?: string;
}

const NODE_LIMIT = 2000; // 软上限，超过警告
const LOD_THRESHOLD = 500; // LOD 折叠阈值

export function useGraphExplore(ontology: string) {
  const [nodes, setNodes] = useState<Map<string, CanvasNode>>(new Map());
  const [edges, setEdges] = useState<Map<string, CanvasEdge>>(new Map());
  const [selectedVid, setSelectedVid] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [truncated, setTruncated] = useState(false);
  const [nextCursor, setNextCursor] = useState<string | null>(null);

  // 卸载守卫：SSE Agent 可能在组件卸载后（导航到其他页）仍触发回调，
  // 此时 setState 会卡住后续渲染。所有 async setter 先检查 mounted。
  const mountedRef = useRef(true);
  useEffect(() => {
    mountedRef.current = true;
    return () => {
      mountedRef.current = false;
    };
  }, []);
  const safeSet = <T,>(setter: (v: T) => void) => (v: T) => {
    if (mountedRef.current) setter(v);
  };
  const [baseIR, setBaseIR] = useState<ObjectSetIR | null>(null);
  const [lastEvidenceId, setLastEvidenceId] = useState<string | null>(null);
  const [layerStyle, setLayerStyle] = useState<LayerStyle>(() => {
    // localStorage 持久化图层样式（二期不入库，设计 §3.4）。
    try {
      const saved = localStorage.getItem('gaia:layerStyle');
      if (saved) return { sizeBy: 'fixed', ...JSON.parse(saved) } as LayerStyle;
    } catch { /* ignore */ }
    return { colorBy: 'type', sizeBy: 'fixed' };
  });

  // layerStyle 变化时持久化。
  useEffect(() => {
    try { localStorage.setItem('gaia:layerStyle', JSON.stringify(layerStyle)); } catch { /* ignore */ }
  }, [layerStyle]);
  const undoStackRef = useRef<HistoryEntry[]>([]);

  const nodeCount = nodes.size;
  const edgeCount = edges.size;
  const overLimit = nodeCount > NODE_LIMIT;
  const shouldCollapse = nodeCount > LOD_THRESHOLD;

  /** 加载起始对象集（ObjectType + 可选 filter）。*/
  const loadStartSet = useCallback(
    async (ir: ObjectSetIR, prefetched?: ReasoningResult) => {
      const setLoadingS = safeSet(setLoading);
      const setErrorS = safeSet(setError);
      const setNodesS = safeSet(setNodes);
      const setTruncatedS = safeSet(setTruncated);
      const setNextCursorS = safeSet(setNextCursor);
      const setBaseIRS = safeSet(setBaseIR);
      const setLastEvidenceIdS = safeSet(setLastEvidenceId);
      setLoadingS(true);
      setErrorS(null);
      try {
        const result = prefetched ?? await queryDataFrame(ontology, ir);
        if (!mountedRef.current) return; // 卸载后不更新
        const newNodes = new Map(nodes);
        for (const obj of result.objects) {
          if (!newNodes.has(obj.rid)) {
            newNodes.set(obj.rid, { ...obj, expandedLinks: new Set() });
          }
        }
        setNodesS(newNodes);
        setTruncatedS(result.truncated);
        setNextCursorS(result.next_cursor);
        setBaseIRS(ir);
        setLastEvidenceIdS(result.evidence_id);
      } catch (e) {
        setErrorS(e instanceof Error ? e.message : '加载失败');
      } finally {
        setLoadingS(false);
      }
    },
    [ontology, nodes],
  );

  /** 应用 AG-UI Agent 发来的画布快照（ADR-015 探索轨迹）。
   *
   * 与 loadStartSet 的区别：同时合并探索边（searchAround 产生的 source→target
   * 关系），多步串联形成可视化轨迹（如 S000→物料→订单）。节点需水合全量属性
   * （canvas.objects 是轻量的，只有 rid/api_name/title/summary），用 static IR
   * 批量点查；边直接增量合并去重。
   *
   * - canvas.edges 非空（探索查询）：节点增量合并（保留既有），边增量去重合并。
   * - canvas.edges 为空（纯查询）：节点覆盖刷新（重新开始看一个对象集），清空边。
   */
  const applyCanvasSnapshot = useCallback(
    async (canvas: CanvasSnapshot) => {
      const rids = canvas.objects.map((o) => o.rid);
      const hasEdges = (canvas.edges?.length ?? 0) > 0;
      setLoading(true);
      setError(null);
      try {
        if (rids.length === 0) {
          // 空结果：纯查询清空画布；探索查询保留既有（0 命中不破坏轨迹）
          if (!hasEdges) {
            setNodes(new Map());
            setEdges(new Map());
          }
          return;
        }
        // 水合全量属性（canvas.objects 轻量，需点查补全 props）。
        // 探索查询时，边的端点（如源节点 S000）可能不在 canvas.objects 里
        // （searchAround 只返回目标对象），需一并水合，否则画布加边时缺源节点。
        const edgeEndpointVids = hasEdges
          ? Array.from(
              new Set(
                (canvas.edges ?? []).flatMap((e) => [e.source_rid, e.target_rid]),
              ),
            )
          : [];
        const allVids = Array.from(new Set([...rids, ...edgeEndpointVids]));
        const result = await queryDataFrame(ontology, { type: 'static', objects: allVids });
        if (!mountedRef.current) return;
        setNodes((prev) => {
          // 探索查询：增量合并（保留既有节点）；纯查询：覆盖
          const next = hasEdges ? new Map(prev) : new Map<string, CanvasNode>();
          for (const obj of result.objects) {
            const existing = next.get(obj.rid);
            next.set(obj.rid, {
              ...obj,
              expandedLinks: existing?.expandedLinks ?? new Set(),
            });
          }
          return next;
        });
        setEdges((prev) => {
          const next = hasEdges ? new Map(prev) : new Map<string, CanvasEdge>();
          for (const e of canvas.edges ?? []) {
            const id = `${e.source_rid}:${e.link_type}:${e.target_rid}`;
            if (!next.has(id)) {
              next.set(id, {
                id,
                source: e.source_rid,
                target: e.target_rid,
                linkType: e.link_type,
              });
            }
          }
          return next;
        });
        setTruncated(result.truncated);
        setNextCursor(result.next_cursor);
        setLastEvidenceId(result.evidence_id);
      } catch (e) {
        setError(e instanceof Error ? e.message : '加载失败');
      } finally {
        setLoading(false);
      }
    },
    [ontology],
  );

  /** Search Around：从选中节点出发单跳遍历，增量加边。 */
  const searchAround = useCallback(
    async (rid: string, linkType: string, direction: 'forward' | 'reverse' = 'forward') => {
      const setLoadingS = safeSet(setLoading);
      const setErrorS = safeSet(setError);
      const setNodesS = safeSet(setNodes);
      const setEdgesS = safeSet(setEdges);
      setLoadingS(true);
      setErrorS(null);
      try {
        const result = await traverseLink(ontology, {
          link_type: linkType,
          source_keys: [rid],
          direction,
          include_source_mapping: true,
        });
        if (!mountedRef.current) return; // 卸载后不更新
        if (result.error) {
          setErrorS(result.error.message);
          return;
        }
        const newNodes = new Map(nodes);
        const newEdges = new Map(edges);
        const addedVids: string[] = [];
        const addedEdgeIds: string[] = [];
        for (const obj of result.target_objects) {
          if (!newNodes.has(obj.rid)) {
            newNodes.set(obj.rid, { ...obj, expandedLinks: new Set() });
            addedVids.push(obj.rid);
          }
          // 加边（source→target 或 reverse）
          const [src, tgt] =
            direction === 'forward' ? [rid, obj.rid] : [obj.rid, rid];
          const edgeId = `${src}:${linkType}:${tgt}`;
          if (!newEdges.has(edgeId)) {
            newEdges.set(edgeId, { id: edgeId, source: src, target: tgt, linkType });
            addedEdgeIds.push(edgeId);
          }
        }
        // 标记源节点已展开此 link
        const srcNode = newNodes.get(rid);
        if (srcNode) {
          srcNode.expandedLinks.add(linkType);
        }
        setNodesS(newNodes);
        setEdgesS(newEdges);
        undoStackRef.current.push({ addedNodeVids: addedVids, addedEdgeIds: addedEdgeIds });
      } catch (e) {
        setErrorS(e instanceof Error ? e.message : 'Search Around 失败');
      } finally {
        setLoadingS(false);
      }
    },
    [ontology, nodes, edges],
  );

  /** 撤销上一次 Search Around。 */
  const undo = useCallback(() => {
    const entry = undoStackRef.current.pop();
    if (!entry) return;
    setNodes((prev) => {
      const next = new Map(prev);
      for (const rid of entry.addedNodeVids) {
        next.delete(rid);
      }
      return next;
    });
    setEdges((prev) => {
      const next = new Map(prev);
      for (const id of entry.addedEdgeIds) {
        next.delete(id);
      }
      return next;
    });
  }, []);

  /** 从画布移除节点。 */
  const removeNode = useCallback((rid: string) => {
    setNodes((prev) => {
      const next = new Map(prev);
      next.delete(rid);
      return next;
    });
    setEdges((prev) => {
      const next = new Map(prev);
      for (const [id, edge] of next) {
        if (edge.source === rid || edge.target === rid) {
          next.delete(id);
        }
      }
      return next;
    });
    setSelectedVid((cur) => (cur === rid ? null : cur));
  }, []);

  /** 加载更多（分页）：用 cursor 取下一页，增量加到画布。 */
  const loadMore = useCallback(async () => {
    if (!baseIR || !nextCursor) return;
    const setLoadingS = safeSet(setLoading);
    const setErrorS = safeSet(setError);
    const setNodesS = safeSet(setNodes);
    const setTruncatedS = safeSet(setTruncated);
    const setNextCursorS = safeSet(setNextCursor);
    setLoadingS(true);
    setErrorS(null);
    try {
      const result = await queryDataFrame(ontology, baseIR, nextCursor);
      if (!mountedRef.current) return; // 卸载后不更新
      const newNodes = new Map(nodes);
      for (const obj of result.objects) {
        if (!newNodes.has(obj.rid)) {
          newNodes.set(obj.rid, { ...obj, expandedLinks: new Set() });
        }
      }
      setNodesS(newNodes);
      setTruncatedS(result.truncated);
      setNextCursorS(result.next_cursor);
    } catch (e) {
      setErrorS(e instanceof Error ? e.message : '加载更多失败');
    } finally {
      setLoadingS(false);
    }
  }, [ontology, baseIR, nextCursor, nodes]);

  /** 清空画布。 */
  const clear = useCallback(() => {
    setNodes(new Map());
    setEdges(new Map());
    setSelectedVid(null);
    setTruncated(false);
    setNextCursor(null);
    setBaseIR(null);
    setLastEvidenceId(null);
    undoStackRef.current = [];
  }, []);

  /** 刷新单个节点的属性（Action 执行后 read-your-writes，Phase 2e）。 */
  const refreshNode = useCallback(
    async (rid: string) => {
      try {
        const result = await queryDataFrame(ontology, { type: 'static', objects: [rid] });
        if (!mountedRef.current) return; // 卸载后不更新
        if (result.objects.length > 0) {
          setNodes((prev) => {
            const next = new Map(prev);
            const existing = next.get(rid);
            if (existing) {
              next.set(rid, { ...result.objects[0], expandedLinks: existing.expandedLinks });
            }
            return next;
          });
        }
      } catch {
        // 静默失败：刷新失败不阻断用户，节点保持旧属性
      }
    },
    [ontology],
  );

  return {
    ontology,
    nodes,
    edges,
    nodeCount,
    edgeCount,
    selectedVid,
    loading,
    error,
    truncated,
    nextCursor,
    lastEvidenceId,
    layerStyle,
    overLimit,
    shouldCollapse,
    setSelectedVid,
    setLayerStyle,
    setError,
    loadStartSet,
    loadMore,
    searchAround,
    applyCanvasSnapshot,
    undo,
    removeNode,
    refreshNode,
    clear,
  };
}
