import { http } from "@/utils/http";
import type { EngineType } from "./agentDefinitions";

export type AgentInstanceResponse = {
  id: string;
  name: string;
  description: string;
  definition_id: string;
  definition_name: string;
  version_id: string | null;
  version_no: string | null;
  definition_current_version_id: string | null;
  has_newer_version: boolean;
  resource_pool_id: string;
  resource_pool_name: string;
  engine_type: EngineType | null;
  group_id: string;
  group_name: string;
  status: "DRAFT" | "PUBLISHED" | "OFFLINE";
  litellm_config: Record<string, any>;
  dify_config?: {
    base_url: string;
    app_type: "chat" | "agent" | "workflow" | "";
    app_api_key: string; // 后端已掩码，如 app-****2345
    app_id: string;
    app_name: string;
    source: "console" | "manual" | "";
    external: boolean;
  };
  runtime_config?: {
    browser_sandbox?: { enabled?: boolean };
  };
  created_by: string;
  creator_name: string;
  created_at: string;
  updated_at: string;
  published_at: string | null;
};

export type AgentInstanceListResponse = {
  items: AgentInstanceResponse[];
  total: number;
  page: number;
  page_size: number;
};

// ── CRUD ────────────────────────────────────

export type CreateInstancePayload = {
  name: string;
  description?: string;
  definition_id: string;
  version_id?: string;
  resource_pool_id: string;
  group_id: string;
  dify_config?: {
    base_url: string;
    app_api_key: string;
    app_type: "chat" | "agent" | "workflow";
    app_id?: string;
    app_name?: string;
    source?: "console" | "manual";
  };
  runtime_config?: {
    browser_sandbox?: { enabled?: boolean };
  };
};

export const getInstancesApi = (params?: Record<string, any>) => {
  return http.request<AgentInstanceListResponse>("get", "/api/manager/agent-instances", {
    params
  });
};

export const createInstanceApi = (data: CreateInstancePayload) => {
  return http.request<AgentInstanceResponse>("post", "/api/manager/agent-instances", { data });
};

export const getInstanceApi = (id: string) => {
  return http.request<AgentInstanceResponse>("get", `/api/manager/agent-instances/${id}`);
};

export const updateInstanceApi = (id: string, data: Record<string, any>) => {
  return http.request<AgentInstanceResponse>("put", `/api/manager/agent-instances/${id}`, { data });
};

export const deleteInstanceApi = (id: string) => {
  return http.request<any>("delete", `/api/manager/agent-instances/${id}`);
};

// ── 业务生命周期 ────────────────────────────────────

export const publishInstanceApi = (id: string) => {
  return http.request<AgentInstanceResponse>("post", `/api/manager/agent-instances/${id}/publish`);
};

export const offlineInstanceApi = (id: string) => {
  return http.request<AgentInstanceResponse>("post", `/api/manager/agent-instances/${id}/offline`);
};

export const switchVersionApi = (id: string, versionId: string) => {
  return http.request<AgentInstanceResponse>(
    "post",
    `/api/manager/agent-instances/${id}/switch-version?version_id=${versionId}`
  );
};

export type UpgradeVersionResult = {
  applied: boolean;
  reason?: string;
  version_id: string;
  changed: string[];
  restarted: boolean;
  message: string;
};

export const upgradeInstanceApi = (id: string, versionId: string) => {
  return http.request<UpgradeVersionResult>(
    "post",
    `/api/manager/agent-instances/${id}/upgrade?version_id=${versionId}`,
    // model_group 变更时含一次 rollout restart（~30-60s），放宽超时
    { timeout: 90000 }
  );
};

export const cloneInstanceApi = (id: string) => {
  return http.request<AgentInstanceResponse>("post", `/api/manager/agent-instances/${id}/clone`);
};

// ── 运行时生命周期（代理调 controller）────────────────────

export type DeploymentStatus = {
  agent_id: string;
  status: "PENDING" | "DEPLOYING" | "RUNNING" | "SUSPENDED" | "FAILED" | "ARCHIVED";
  engine_url: string | null;
  last_active_at: string | null;
  error_message: string | null;
  pod_name?: string | null;
  pod_start_time?: string | null;
  pod_phase?: string | null;
};

export const getDeploymentStatusApi = (instanceId: string) => {
  return http.request<DeploymentStatus>(
    "get",
    `/api/manager/agent-instances/${instanceId}/deployment-status`
  );
};

export const deployInstanceApi = (instanceId: string) => {
  return http.request<{ status: string; message: string }>(
    "post",
    `/api/manager/agent-instances/${instanceId}/deploy`
  );
};

export const suspendInstanceApi = (instanceId: string) => {
  return http.request<{ status: string; message: string }>(
    "post",
    `/api/manager/agent-instances/${instanceId}/suspend`
  );
};

export const resumeInstanceApi = (instanceId: string) => {
  return http.request<{ status: string; message: string }>(
    "post",
    `/api/manager/agent-instances/${instanceId}/resume`
  );
};

export const restartInstanceApi = (instanceId: string) => {
  return http.request<{ status: string; message: string }>(
    "post",
    `/api/manager/agent-instances/${instanceId}/restart`,
    // fan-out 写各 Pod config.yaml + rollout restart，实测 ~12s，默认 10s 会误报超时
    { timeout: 60000 }
  );
};

export const destroyInstanceApi = (instanceId: string) => {
  return http.request<{ status: string; message: string }>(
    "post",
    `/api/manager/agent-instances/${instanceId}/destroy`
  );
};

// ── 运行状态 / Pod / 日志 ────────────────────────────────────

export type InstancePod = {
  name: string;
  node: string;
  status: "Running" | "Pending" | "CrashLoopBackOff" | "Terminating" | string;
  cpu: string;
  memory: string;
  restarts: number;
  age: string;
  agent_id?: string;
  agent_name?: string;
  created_at: string;
};

export type InstancePodsResponse = {
  items: InstancePod[];
  summary: { running: number; stopped: number; abnormal: number };
};

export const getInstancePodsApi = (instanceId: string) => {
  return http.request<InstancePodsResponse>("get", `/api/manager/agent-instances/${instanceId}/pods`);
};

export type PodLogProfile = {
  profile_name: string;
  username: string | null;
  real_name: string | null;
};

export type InstancePodLogSources = {
  engine: boolean;
  profiles: PodLogProfile[];
};

export const getInstancePodLogsApi = (
  instanceId: string,
  podName: string,
  opts?: { tailLines?: number; source?: "engine" | "gateway"; profile?: string }
) => {
  return http.request<{ pod_name: string; logs: string; source?: string; profile?: string; profiles?: PodLogProfile[] }>(
    "get",
    `/api/manager/agent-instances/${instanceId}/pods/${podName}/logs`,
    { params: { tail_lines: opts?.tailLines, source: opts?.source, profile: opts?.profile } }
  );
};

export const getInstancePodLogSourcesApi = (instanceId: string, podName: string) => {
  return http.request<InstancePodLogSources>(
    "get",
    `/api/manager/agent-instances/${instanceId}/pods/${podName}/logs/sources`
  );
};

// ── 监控 / 概览 ────────────────────────────────────

export type MetricPoint = { timestamp: string; value: number };

export type InstanceMetrics = {
  cpu: MetricPoint[];
  memory: MetricPoint[];
  requests: MetricPoint[];
  tokens: { input: MetricPoint[]; output: MetricPoint[] };
  resourceRequest?: { cpu_m: number; memory_mi: number };
  attribution?: Record<string, any>;
};

export type InstanceOverview = {
  conversationCount: number;
  totalTokens: number;
  activeUsers: number;
  conversationTrend: MetricPoint[];
};

export const getInstanceMetricsApi = (
  instanceId: string,
  params?: { range?: "1h" | "6h" | "24h" | "7d" }
) => {
  return http.request<InstanceMetrics>("get", `/api/manager/agent-instances/${instanceId}/metrics`, {
    params
  });
};

export const getInstanceOverviewApi = (instanceId: string) => {
  return http.request<InstanceOverview>("get", `/api/manager/agent-instances/${instanceId}/overview`);
};

// ── 渠道（挂在实例层）────────────────────────────────────

export type ChannelResponse = {
  id: string;
  instance_id: string;
  channel_type: "wecom" | "wecom_bot_callback" | "feishu" | "dingtalk" | "http";
  scope_type: string;
  scope_target_id: string | null;
  profile_type: string;
  enabled: boolean;
  callback_url?: string;
  config?: Record<string, string>;
  created_at: string;
  updated_at: string;
};

export type ChannelListResponse = { items: ChannelResponse[]; total: number };

export type ChannelCreatePayload = {
  channel_type: string;
  config: Record<string, string>;
};

export type ChannelUpdatePayload = {
  config?: Record<string, string>;
  enabled?: boolean;
};

export const getChannelsApi = (instanceId: string) => {
  return http.request<ChannelListResponse>("get", `/api/manager/agent-instances/${instanceId}/channels`);
};

export const createChannelApi = (instanceId: string, data: ChannelCreatePayload) => {
  return http.request<ChannelResponse>("post", `/api/manager/agent-instances/${instanceId}/channels`, {
    data
  });
};

export const updateChannelApi = (instanceId: string, channelId: string, data: ChannelUpdatePayload) => {
  return http.request<ChannelResponse>(
    "put",
    `/api/manager/agent-instances/${instanceId}/channels/${channelId}`,
    { data }
  );
};

export const deleteChannelApi = (instanceId: string, channelId: string) => {
  return http.request<any>("delete", `/api/manager/agent-instances/${instanceId}/channels/${channelId}`);
};

// ── API Keys（OpenAI 兼容，挂在实例层）─────────────────────────────

export type ApiKeyResponse = {
  id: string;
  instance_id: string;
  name: string;
  key_prefix: string; // 前 14 字符，如 sk-abcd1234efgh
  last_used_at: string | null;
  last_used_ip: string | null;
  created_at: string;
};

export type ApiKeyListResponse = {
  items: ApiKeyResponse[];
  total: number;
};

export type ApiKeyCreateResponse = {
  id: string;
  name: string;
  key_prefix: string;
  key: string; // 明文，仅创建时返回一次
  created_at: string;
};

export const getApiKeysApi = (instanceId: string) => {
  return http.request<ApiKeyListResponse>(
    "get",
    `/api/manager/agent-instances/${instanceId}/api-keys`
  );
};

export const createApiKeyApi = (
  instanceId: string,
  data: { name: string }
) => {
  return http.request<ApiKeyCreateResponse>(
    "post",
    `/api/manager/agent-instances/${instanceId}/api-keys`,
    { data }
  );
};

export const deleteApiKeyApi = (instanceId: string, keyId: string) => {
  return http.request<any>(
    "delete",
    `/api/manager/agent-instances/${instanceId}/api-keys/${keyId}`
  );
};
