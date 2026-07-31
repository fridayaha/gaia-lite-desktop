/**
 * CanvasSnapshot — 图探索画布的 AG-UI shared state（ADR-015）。
 *
 * 与后端 `ontology.core.schemas.canvas.CanvasSnapshot` 对齐（snake_case）。
 * Agent 通过工具返回 STATE_SNAPSHOT 事件写入此 state；前端 useAgent 订阅
 * state.canvas 变化 → 调 useGraphExplore 的 loadStartSet/setView/setLayerStyle
 * 同步画布。Agent 每轮读 state 决策（ReAct observe）。
 */

export interface CanvasObject {
  rid: string;
  api_name: string;
  title: string;
  summary: Record<string, unknown>;
}

/** 画布探索轨迹边（searchAround 产生的关系箭头，ADR-015）。
 * 只有明确关系链的探索才产生边；纯查询不产生边。 */
export interface CanvasEdge {
  source_rid: string;
  target_rid: string;
  link_type: string;
  direction: 'out' | 'in' | 'both';
}

export interface CanvasSnapshot {
  objects: CanvasObject[];
  edges: CanvasEdge[];
  view: 'graph' | 'map' | 'split';
  color_by: string | null;
  expanded_links: string[];
  object_count: number;
  last_query_summary: string;
}

/** AG-UI shared state 顶层结构：{ canvas: CanvasSnapshot }。
 * 后端工具返回的 snapshot 是 { canvas: {...} }，前端读 state.canvas。 */
export interface GraphExploreState {
  canvas: CanvasSnapshot;
}

export const EMPTY_CANVAS: CanvasSnapshot = {
  objects: [],
  edges: [],
  view: 'graph',
  color_by: null,
  expanded_links: [],
  object_count: 0,
  last_query_summary: '',
};
