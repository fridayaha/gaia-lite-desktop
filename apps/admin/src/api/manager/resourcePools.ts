import { http } from "@/utils/http";

export type ResourcePoolResponse = {
  id: string;
  name: string;
  description: string;
  group_id: string | null;
  group_name: string | null;
  min_cpu: string;
  max_cpu: string;
  min_memory: string;
  max_memory: string;
  min_replicas: number;
  max_replicas: number;
  max_sessions_per_pod: number;
  auto_recycle: boolean;
  idle_suspend_minutes: number;
  idle_destroy_hours: number;
  created_by: string;
  creator_name: string;
  instance_count: number;
  created_at: string;
  updated_at: string;
};

export type ResourcePoolListResponse = {
  items: ResourcePoolResponse[];
  total: number;
  page: number;
  page_size: number;
};

export type ResourcePoolCreatePayload = {
  name: string;
  description?: string;
  group_id?: string | null; // null/不传=平台共享池；指定=组私有池
  min_cpu?: string;
  max_cpu?: string;
  min_memory?: string;
  max_memory?: string;
  min_replicas?: number;
  max_replicas?: number;
  max_sessions_per_pod?: number;
  auto_recycle?: boolean;
  idle_suspend_minutes?: number;
  idle_destroy_hours?: number;
};

export const getResourcePoolsApi = (params?: Record<string, any>) => {
  return http.request<ResourcePoolListResponse>("get", "/api/manager/resource-pools", { params });
};

export const createResourcePoolApi = (data: ResourcePoolCreatePayload) => {
  return http.request<ResourcePoolResponse>("post", "/api/manager/resource-pools", { data });
};

export const getResourcePoolApi = (id: string) => {
  return http.request<ResourcePoolResponse>("get", `/api/manager/resource-pools/${id}`);
};

export const updateResourcePoolApi = (id: string, data: Record<string, any>) => {
  return http.request<ResourcePoolResponse>("put", `/api/manager/resource-pools/${id}`, { data });
};

export const deleteResourcePoolApi = (id: string) => {
  return http.request<any>("delete", `/api/manager/resource-pools/${id}`);
};

export const cloneResourcePoolApi = (id: string) => {
  return http.request<ResourcePoolResponse>("post", `/api/manager/resource-pools/${id}/clone`);
};

// ── Pods / 监控 / 日志 ────────────────────────────────────

export type PoolPod = {
  name: string;
  node: string;
  status: string;
  cpu: string;
  memory: string;
  restarts: number;
  age: string;
  agent_id?: string;
  agent_name?: string;
  created_at: string;
};

export type PoolPodsResponse = {
  items: PoolPod[];
  summary: { running: number; stopped: number; abnormal: number };
};

export type PoolMetrics = {
  cpu: { timestamp: string; value: number }[];
  memory: { timestamp: string; value: number }[];
  resourceRequest: { cpu_m: number; memory_mi: number };
  podCount: number;
};

export const getPoolPodsApi = (poolId: string) => {
  return http.request<PoolPodsResponse>("get", `/api/manager/resource-pools/${poolId}/pods`);
};

export type PodLogProfile = {
  profile_name: string;
  username: string | null;
  real_name: string | null;
};

export type PoolPodLogSources = {
  engine: boolean;
  profiles: PodLogProfile[];
};

export const getPoolPodLogsApi = (
  poolId: string,
  podName: string,
  opts?: { tailLines?: number; source?: "engine" | "gateway"; profile?: string }
) => {
  return http.request<{ pod_name: string; logs: string; source?: string; profile?: string; profiles?: PodLogProfile[] }>(
    "get",
    `/api/manager/resource-pools/${poolId}/pods/${podName}/logs`,
    { params: { tail_lines: opts?.tailLines, source: opts?.source, profile: opts?.profile } }
  );
};

export const getPoolPodLogSourcesApi = (poolId: string, podName: string) => {
  return http.request<PoolPodLogSources>(
    "get",
    `/api/manager/resource-pools/${poolId}/pods/${podName}/logs/sources`
  );
};

export const getPoolMetricsApi = (
  poolId: string,
  params?: { range?: "1h" | "6h" | "24h" | "7d" }
) => {
  return http.request<PoolMetrics>("get", `/api/manager/resource-pools/${poolId}/metrics`, {
    params
  });
};
