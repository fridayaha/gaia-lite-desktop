import { http } from "@/utils/http";

/** 链路追踪条目 */
export interface TraceItem {
  id: string;
  name: string;
  agent_id: string | null;
  session_id: string | null;
  enduser_id: string | null;
  channel_type: string | null;
  created_at: string | null;
  latency_ms: number | null;
  ttft_ms: number | null;
  avg_incremental_ms: number | null;
  token_total: number;
  token_input: number;
  token_output: number;
  observation_count: number;
  status: "ok" | "error";
  metadata: Record<string, unknown>;
}

/** 链路列表响应 */
export interface TraceListResponse {
  items: TraceItem[];
  total: number;
  langfuse_configured: boolean;
  langfuse_url?: string;
}

/** trace 详情 */
export interface TraceDetail {
  id: string;
  name: string;
  userId: string | null;
  sessionId: string | null;
  timestamp: string | null;
  createdAt: string | null;
  latency: number | null;  // 秒（v3 顶层字段）
  totalCost: number | null;
  input: unknown;
  output: unknown;
  metadata: Record<string, unknown>;
}

/** observation 条目（trace 详情里的子节点） */
export interface ObservationItem {
  id: string;
  type: "SPAN" | "GENERATION" | "EVENT" | string;
  name?: string;
  startTime: string | null;
  endTime: string | null;
  completionStartTime?: string | null;
  model?: string;
  level: "DEBUG" | "DEFAULT" | "WARNING" | "ERROR" | string;
  usage?: {
    input?: number;
    output?: number;
    total?: number;
    unit?: string;
  } | null;
  calculatedTotalCost?: number;
  input?: unknown;
  output?: unknown;
  metadata?: Record<string, unknown>;
}

/** trace 详情响应 */
export interface TraceDetailResponse {
  trace: TraceDetail | null;
  observations: ObservationItem[];
  langfuse_configured: boolean;
  langfuse_url?: string;
}

/** 用量分析 by_agent 条目 */
export interface UsageByAgent {
  agent_id: string;
  name?: string;
  conversation_count: number;
  total_tokens: number;
}

/** 用量分析 by_model 条目 */
export interface UsageByModel {
  model: string;
  total_tokens: number;
  total_cost: number;
}

/** 用量分析 by_group 条目 */
export interface UsageByGroup {
  group_id: string;
  name: string;
  conversation_count: number;
  total_tokens: number;
  total_cost: number;
}

/** 用量趋势点 */
export interface UsageTrendPoint {
  date: string;
  tokens: number;
  cost: number;
}

/** 用量分析响应 */
export interface UsageResponse {
  today_tokens: number;
  monthly_tokens: number;
  monthly_cost: number;
  by_agent: UsageByAgent[];
  by_model: UsageByModel[];
  by_group: UsageByGroup[];
  trend: UsageTrendPoint[];
  litellm_url: string;
}

/** 调用分析 by_agent 条目 */
export interface QualityByAgent {
  agent_id: string;
  name?: string;
  request_count: number;
  success_rate: number;
  p50_latency_ms: number;
  p95_latency_ms: number;
  avg_tokens: number;
}

/** 调用分析响应 */
export interface QualityResponse {
  langfuse_configured: boolean;
  overall: {
    request_count: number;
    success_rate: number;
    p50_latency_ms: number;
    p95_latency_ms: number;
    avg_tokens_per_request: number;
  };
  by_agent: QualityByAgent[];
  langfuse_url?: string;
}

/** 异常告警条目 */
export interface AlertItem {
  type: AlertRuleType;
  category: AlertRuleCategory;
  severity: AlertSeverity;
  agent_id: string;
  trace_id: string;
  message: string;
  created_at: string | null;
}

/** 异常告警响应 */
export interface AlertsResponse {
  items: AlertItem[];
  total: number;
  langfuse_configured: boolean;
  langfuse_url?: string;
}

/** 异常事件状态（0.8.66 状态机） */
export type AlertEventStatus = "firing" | "resolved" | "acknowledged";

/** 异常事件条目（/alert-events 端点，历史事件表） */
export interface AlertEventItem {
  id: string;
  rule_id: string | null;
  rule_name: string;
  rule_type: AlertRuleType;
  trace_id: string | null;
  agent_id: string | null;
  severity: AlertSeverity;
  message: string;
  notified_channels: Array<{ type: string; name?: string; ok: boolean; error?: string }>;
  created_at: string | null;
  status: AlertEventStatus;
  acknowledged_by: string | null;
  acknowledged_at: string | null;
  last_seen_at: string | null;
  resolved_at: string | null;
}

/** 异常事件列表响应（{code, message, data} 包装） */
export interface AlertEventsResponse {
  code: number;
  message: string;
  data?: {
    list: AlertEventItem[];
    total: number;
    pageSize: number;
    currentPage: number;
    stats: {
      critical: number;
      warning: number;
      firing: number;
      resolved: number;
      acknowledged: number;
    };
    langfuse_configured: boolean;
    langfuse_url?: string;
  };
}

export const getTracesApi = (params: { agent_id?: string; enduser_id?: string; channel_type?: string; session_id?: string; from_ts?: string; to_ts?: string; limit?: number; offset?: number }) => {
  return http.request<TraceListResponse>("get", "/api/manager/observability/traces", { params });
};

export const getTraceDetailApi = (traceId: string) => {
  return http.request<TraceDetailResponse>("get", `/api/manager/observability/traces/${traceId}`);
};

/** Hermes 关联查询响应 */
export interface HermesCorrelationResponse {
  /** 已匹配上的 Hermes 内层 trace；未匹配为 null */
  hermes_trace: TraceDetail | null;
  /** Hermes trace 的 observations 列表 */
  observations: ObservationItem[];
  langfuse_configured: boolean;
  langfuse_url?: string;
  /** 匹配结果原因：matched / no_matching_hermes_trace / no_correlation_keys_in_gateway_trace /
   *  gateway_trace_not_found / langfuse_not_configured / list_traces_failed /
   *  invalid_gateway_request_time / direct_llm_call（/v1/chat/completions 走直接
   *  LLM 代理，Hermes 不进 agent loop，无内部 trace） */
  reason: string;
  /** Gateway trace 的 metadata（含 last_user_message_hash + gateway_request_time） */
  gateway_metadata?: Record<string, unknown>;
  /** 未匹配时返回候选 trace 数量，便于前端显示调试信息 */
  candidate_count?: number;
}

/** 查询 Gateway trace 关联的 Hermes 内层 trace + observations */
export const getHermesCorrelationApi = (traceId: string) => {
  return http.request<HermesCorrelationResponse>(
    "get",
    `/api/manager/observability/traces/${traceId}/hermes-correlation`
  );
};

export const getUsageApi = (params: {
  days?: number;
  agent_id?: string;
  enduser_id?: string;
  user_group_id?: string;
  from_ts?: string;
  to_ts?: string;
} = {}) => {
  return http.request<UsageResponse>("get", "/api/manager/observability/usage", { params });
};

export const getQualityApi = (params: {
  agent_id?: string;
  enduser_id?: string;
  user_group_id?: string;
  from_ts?: string;
  to_ts?: string;
} = {}) => {
  return http.request<QualityResponse>("get", "/api/manager/observability/quality", { params });
};

export const getAlertsApi = (params: { limit?: number }) => {
  return http.request<AlertsResponse>("get", "/api/manager/observability/alerts", { params });
};

/** 异常事件列表（历史事件表，支持分页 + severity/status/ruleTypes 过滤 + stats 聚合） */
export const getAlertEventsApi = (params: {
  pageSize?: number;
  currentPage?: number;
  severity?: string;
  status?: string;
  rule_types?: string;  // 逗号分隔多值，后端按 rule_type IN 过滤
}) => {
  return http.request<AlertEventsResponse>("get", "/api/manager/observability/alert-events", { params });
};

/** 标记事件为已确认（acknowledged）—— 不发重复通知，但不影响 firing/resolved 状态 */
export const acknowledgeAlertEventApi = (eventId: string) => {
  return http.request<{ code: number; message: string }>(
    "post",
    `/api/manager/observability/alert-events/${eventId}/acknowledge`
  );
};

/** 资源监控 — 集群概览 */
export interface ResourceCluster {
  cpu_pct: number;
  memory_pct: number;
  pod_count: number;
}

/** 资源监控 — 趋势点 */
export interface ResourceTrendPoint {
  ts: number;
  cpu_pct: number;
  memory_pct: number;
}

/** 资源监控 — Top 节点 */
export interface ResourceTopNode {
  instance: string;
  cpu_pct: number;
  memory_pct: number;
  disk_pct: number;
}

/** 资源监控 — Top Pod */
export interface ResourceTopPod {
  pod: string;
  namespace: string;
  cpu_used_cores: number;
  memory_used_mb: number;
  restarts: number;
}

/** 资源监控响应 */
export interface ResourcesResponse {
  metrics_available: boolean;
  range: string;
  cluster: ResourceCluster;
  trend: ResourceTrendPoint[];
  top_nodes: ResourceTopNode[];
  top_pods: ResourceTopPod[];
  firing_alerts: number;
  grafana_url: string;
}

export const getResourcesApi = (params: { range?: "1h" | "6h" | "24h" | "7d"; start_ts?: number; end_ts?: number }) => {
  return http.request<ResourcesResponse>("get", "/api/manager/observability/resources", { params });
};

/** 服务健康 — 单个服务条目 */
export interface ServiceHealthItem {
  name: string;
  status: "ok" | "down";
  latency_ms: number | null;
  p50_ms: number | null;
  p95_ms: number | null;
  uptime_pct: number;
  slo_met: boolean;
  last_down_ts: number | null;
  is_tcp: boolean;
}

/** 服务健康 — 总体统计 */
export interface ServiceHealthOverall {
  up_count: number;
  total_count: number;
  avg_p95_ms: number | null;
  avg_uptime_pct: number;
}

/** 服务健康 — 趋势点 */
export interface ServiceHealthTrendPoint {
  ts: number;
  latencies: Record<string, number | null>;
}

/** 服务健康 — 响应 */
export interface ServiceHealthResponse {
  metrics_available: boolean;
  range: string;
  overall: ServiceHealthOverall;
  items: ServiceHealthItem[];
  trend: ServiceHealthTrendPoint[];
  grafana_url: string;
  grafana_dashboard_uid: string;
}

export const getServiceHealthApi = (params: {
  range?: "1h" | "6h" | "24h" | "7d";
  start_ts?: number;
  end_ts?: number;
}) => {
  return http.request<ServiceHealthResponse>("get", "/api/manager/observability/service-health", { params });
};

/** 热门智能体 Top N（数据源 Langfuse traces，1 trace = 1 次对话） */
export interface TopAgentItem {
  agent_id: string;
  name: string;
  conversation_count: number;
  total_tokens: number;
}

export interface TopAgentsResponse {
  langfuse_configured: boolean;
  items: TopAgentItem[];
}

export const getTopAgentsApi = (params: { limit?: number; days?: number } = {}) => {
  return http.request<TopAgentsResponse>("get", "/api/manager/observability/top-agents", {
    params: { limit: params.limit ?? 5, days: params.days ?? 30 }
  });
};

// ── 操作日志（OperationLog 全量审计查询） ───────────────────

export interface OperationLogItem {
  id: string;
  action: string;
  target_type: string;
  target_id: string | null;
  target_name: string | null;
  status: string;
  detail: Record<string, any> | null;
  group_id: string | null;
  actor_id: string | null;
  actor_name: string | null;
  actor_real_name: string | null;
  operator_ip: string | null;
  operator_user_agent: string | null;
  created_at: string | null;
}

export interface OperationLogListResponse {
  code: number;
  message: string;
  data: {
    list: OperationLogItem[];
    total: number;
    pageSize: number;
    currentPage: number;
  };
}

export const getOperationLogsApi = (params: {
  actor_id?: string;
  action?: string;
  target_type?: string;
  target_id?: string;
  status?: string;
  group_id?: string;
  keyword?: string;
  time_from?: string;
  time_to?: string;
  pageSize?: number;
  currentPage?: number;
} = {}) => {
  return http.request<OperationLogListResponse>("get", "/api/manager/observability/operation-logs", { params });
};

// ── 日志检索（Loki 代理） ───────────────────────────────────

export interface LogSearchItem {
  ts: string;
  service: string;
  level: string;
  logger: string;
  message: string;
  request_id: string;
  user_id: string;
  raw: Record<string, any>;
  loki_ts: string;
}

export interface LogSearchResponse {
  items: LogSearchItem[];
  total: number;
  query: string;
  grafana_url: string;
}

export const searchLogsApi = (params: {
  service?: string;
  level?: string;
  request_id?: string;
  keyword?: string;
  time_from?: string;
  time_to?: string;
  limit?: number;
} = {}) => {
  return http.request<LogSearchResponse>("get", "/api/manager/observability/logs/search", { params });
};

// ── 告警规则配置（AlertRule CRUD） ─────────────────────────

export type AlertRuleCategory =
  | "tracing"
  | "resource"
  | "service_health"
  | "usage"
  | "call_analysis";
export type AlertRuleType =
  // tracing
  | "error_trace"
  | "high_latency"
  | "high_tokens"
  // resource
  | "high_cpu"
  | "high_memory"
  | "high_disk"
  | "pod_restart"
  // service_health
  | "service_down"
  | "high_p95_latency"
  | "low_uptime"
  // usage
  | "high_daily_tokens"
  | "high_monthly_cost"
  | "high_agent_tokens"
  // call_analysis
  | "low_success_rate"
  | "high_p95_call_latency"
  | "high_avg_tokens_per_request";
export type AlertSeverity = "critical" | "warning";
export type AlertChannelType = "feishu" | "dingtalk" | "wecom" | "email";

// 5 大类 label 映射
export const RULE_CATEGORY_LABEL: Record<AlertRuleCategory, string> = {
  tracing: "链路追踪",
  resource: "资源监控",
  service_health: "服务健康",
  usage: "用量分析",
  call_analysis: "调用分析"
};

// rule_type → category 反查
export const RULE_TYPE_CATEGORY: Record<AlertRuleType, AlertRuleCategory> = {
  // tracing
  error_trace: "tracing",
  high_latency: "tracing",
  high_tokens: "tracing",
  // resource
  high_cpu: "resource",
  high_memory: "resource",
  high_disk: "resource",
  pod_restart: "resource",
  // service_health
  service_down: "service_health",
  high_p95_latency: "service_health",
  low_uptime: "service_health",
  // usage
  high_daily_tokens: "usage",
  high_monthly_cost: "usage",
  high_agent_tokens: "usage",
  // call_analysis
  low_success_rate: "call_analysis",
  high_p95_call_latency: "call_analysis",
  high_avg_tokens_per_request: "call_analysis"
};

// rule_type → 中文 label
export const RULE_TYPE_LABEL: Record<AlertRuleType, string> = {
  error_trace: "错误请求",
  high_latency: "延迟超阈值",
  high_tokens: "Token 超阈值",
  high_cpu: "集群 CPU 高",
  high_memory: "集群内存高",
  high_disk: "节点磁盘高",
  pod_restart: "Pod 重启",
  service_down: "服务下线",
  high_p95_latency: "服务 p95 延迟高",
  low_uptime: "服务可用性低",
  high_daily_tokens: "日 Token 超",
  high_monthly_cost: "月费用超",
  high_agent_tokens: "智能体 Token 超",
  low_success_rate: "成功率低",
  high_p95_call_latency: "调用 p95 高",
  high_avg_tokens_per_request: "均 Token 高"
};

// rule_type → 阈值单位
export const RULE_TYPE_UNIT: Record<AlertRuleType, string> = {
  error_trace: "",
  high_latency: "ms",
  high_tokens: "tokens",
  high_cpu: "%",
  high_memory: "%",
  high_disk: "%",
  pod_restart: "次",
  service_down: "",
  high_p95_latency: "ms",
  low_uptime: "%",
  high_daily_tokens: "tokens",
  high_monthly_cost: "USD",
  high_agent_tokens: "tokens",
  low_success_rate: "%",
  high_p95_call_latency: "ms",
  high_avg_tokens_per_request: "tokens"
};

// 无阈值规则（状态命中即触发）
export const RULE_TYPES_NO_THRESHOLD: AlertRuleType[] = ["error_trace", "service_down"];

// 反向比较规则（值低于阈值才触发）
export const RULE_TYPES_INVERTED: AlertRuleType[] = ["low_uptime", "low_success_rate"];

// 按分类组织 rule_type（供前端 select 选项分组用）
export const RULE_TYPES_BY_CATEGORY: Record<AlertRuleCategory, AlertRuleType[]> = {
  tracing: ["error_trace", "high_latency", "high_tokens"],
  resource: ["high_cpu", "high_memory", "high_disk", "pod_restart"],
  service_health: ["service_down", "high_p95_latency", "low_uptime"],
  usage: ["high_daily_tokens", "high_monthly_cost", "high_agent_tokens"],
  call_analysis: ["low_success_rate", "high_p95_call_latency", "high_avg_tokens_per_request"]
};

export interface AlertRuleItem {
  id: string;
  name: string;
  category: AlertRuleCategory;
  rule_type: AlertRuleType;
  threshold: number | null;
  enabled: boolean;
  severity: AlertSeverity;
  description: string | null;
  created_at: string;
  updated_at: string;
}

export interface AlertRuleUpdatePayload {
  name?: string;
  threshold?: number | null;
  enabled?: boolean;
  severity?: AlertSeverity;
  description?: string | null;
}

export const getAlertRulesApi = () => {
  return http.request<AlertRuleItem[]>("get", "/api/manager/observability/alert-rules");
};

export const updateAlertRuleApi = (ruleId: string, payload: AlertRuleUpdatePayload) => {
  return http.request<AlertRuleItem>("put", `/api/manager/observability/alert-rules/${ruleId}`, { data: payload });
};

// ── 告警渠道（AlertChannel CRUD，独立实体订阅规则） ─────────

/** 渠道 config——按 channel_type 分化：
 *  - feishu/dingtalk/wecom: { webhook_url }
 *  - email: { to: string[] }
 */
export interface AlertChannelConfig {
  webhook_url?: string;
  to?: string[];
}

export interface AlertChannelItem {
  id: string;
  name: string;
  channel_type: AlertChannelType;
  config: AlertChannelConfig;
  subscribed_all: boolean;
  subscribed_rule_ids: string[];
  enabled: boolean;
  created_at: string;
  updated_at: string;
}

export interface AlertChannelCreatePayload {
  name: string;
  channel_type: AlertChannelType;
  config: AlertChannelConfig;
  subscribed_all?: boolean;
  subscribed_rule_ids?: string[];
  enabled?: boolean;
}

export interface AlertChannelUpdatePayload {
  name?: string;
  channel_type?: AlertChannelType;
  config?: AlertChannelConfig;
  subscribed_all?: boolean;
  subscribed_rule_ids?: string[];
  enabled?: boolean;
}

export const getAlertChannelsApi = () => {
  return http.request<AlertChannelItem[]>("get", "/api/manager/observability/alert-channels");
};

export const createAlertChannelApi = (payload: AlertChannelCreatePayload) => {
  return http.request<AlertChannelItem>("post", "/api/manager/observability/alert-channels", { data: payload });
};

export const updateAlertChannelApi = (channelId: string, payload: AlertChannelUpdatePayload) => {
  return http.request<AlertChannelItem>("put", `/api/manager/observability/alert-channels/${channelId}`, { data: payload });
};

export const deleteAlertChannelApi = (channelId: string) => {
  return http.request<void>("delete", `/api/manager/observability/alert-channels/${channelId}`);
};
