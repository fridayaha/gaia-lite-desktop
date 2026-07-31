/**
 * Graph reasoning API client (graph-reasoning-frontend-design.md §8).
 *
 * 推理线（query_with_dataframe）+ 关系遍历（traverse_link/exists_link）+
 * 证据链（analysis）+ 时序流式接入（timeseries-sync）。
 * 与 SQL 线（api/client.ts 的 textsql）独立并行。
 */
import { request } from './client';
import type {
  AnalysisRecord,
  ExistsRequest,
  ExistsResult,
  ObjectSetIR,
  ReasoningResult,
  SyncTask,
  TraverseRequest,
  TraverseResult,
} from '../types';

/** 执行 ObjectSet IR 查询（推理线核心）。 */
export function queryDataFrame(
  ontology: string,
  ir: ObjectSetIR,
  cursor?: string,
): Promise<ReasoningResult> {
  const qs = cursor ? `?cursor=${encodeURIComponent(cursor)}` : '';
  return request<ReasoningResult>(`/objects/${ontology}/query-dataframe${qs}`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(ir),
  });
}

/** 单跳关系遍历（画布右键 Search Around 调用）。 */
export function traverseLink(
  ontology: string,
  req: TraverseRequest,
): Promise<TraverseResult> {
  return request<TraverseResult>(`/objects/${ontology}/traverse`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(req),
  });
}

/** 关系存在性检查。 */
export function existsLink(ontology: string, req: ExistsRequest): Promise<ExistsResult> {
  return request<ExistsResult>(`/objects/${ontology}/exists-link`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(req),
  });
}

/** 查证据链快照。 */
export function getAnalysis(ontology: string, id: string): Promise<AnalysisRecord> {
  return request<AnalysisRecord>(`/objects/${ontology}/analysis/${id}`);
}

/** 启动 Kafka → TimescaleDB 时序流式同步。 */
export function startTimeseriesSync(
  datasourceApiName: string,
  req: {
    kafka_topic: string;
    target_hypertable: string;
    schema_fields: Record<string, string>;
    primary_keys?: string[];
    consumer_group?: string;
    task_api_name?: string;
  },
): Promise<SyncTask> {
  return request<SyncTask>(`/api/datasources/${datasourceApiName}/timeseries-sync`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(req),
  });
}

/** 空间过滤：从候选 rid 集中返回命中空间条件的 rid（Phase 2b MapPanel）。 */
export function spatialFilter(
  ontology: string,
  req: {
    object_type: string;
    candidate_rids: string[];
    op: 'withinDistance' | 'withinPolygon' | 'withinBoundingBox';
    center?: [number, number];
    max_distance?: number;
    coords?: [number, number][];
    bbox?: [[number, number], [number, number]];
  },
): Promise<string[]> {
  return request<string[]>(`/objects/${ontology}/spatial-filter`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(req),
  });
}

/** 时序查询：轨迹回放（Phase 2b TrajectoryPlayer）。 */
export function seriesQuery(
  ontology: string,
  req: {
    object_type: string;
    series_property: string;
    series_ids: string[];
    time_start?: string;
    time_end?: string;
    limit?: number;
  },
): Promise<Array<Record<string, unknown>>> {
  return request<Array<Record<string, unknown>>>(`/objects/${ontology}/series-query`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(req),
  });
}

/** 路径推理：源→目标最短路径（Phase 2d find_paths）。 */
export function findPaths(
  ontology: string,
  req: {
    source_key: string;
    target_key: string;
    link_types?: string[];
    max_depth?: number;
    limit?: number;
  },
): Promise<{ source: string; target: string; paths: string[][]; count: number }> {
  return request(`/objects/${ontology}/find-paths`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(req),
  });
}
