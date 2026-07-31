import { http } from "@/utils/http";

/** 最近活动条目 */
export interface DashboardActivity {
  user: string;
  action: string;
  target: string;
  time: string;
  type: "publish" | "create" | "edit" | "offline" | "deploy" | "suspend" | "user" | "install";
}

/** 用户组概览 */
export interface GroupOverview {
  groupName: string;
  agentCount: number;
  memberCount: number;
  todayConversations: number;
  monthlyTokens: number;
  agentDistribution: { name: string; value: number; color: string }[];
}

/** 服务健康探活结果 */
export interface HealthItem {
  name: string;
  status: "ok" | "down" | "undeployed";
  latencyMs: number;
}

/** 全平台资源实时用量 */
export interface PlatformResources {
  cpuUsed: number; // millicores
  cpuLimit: number; // millicores
  memUsed: number; // Mi
  memLimit: number; // Mi
  podCount: number;
  metricsAvailable: boolean; // false 时用量为资源 requests 兜底（已分配）
}

/** Token / 计费概览 */
export interface BillingOverview {
  todayTokens: number;
  monthlyTokens: number;
  monthlyCost: number; // CNY
}

/** 引擎实例状态分布项 */
export interface InstanceStatusItem {
  name: string;
  value: number;
  color: string;
}

/** 热门 Agent 排行项 */
export interface TopAgentItem {
  agent_id: string;
  name: string;
  conversation_count: number; // 近 30 天对话次数
  total_tokens: number;
}

/** 个人对话趋势点 */
export interface ConversationTrendPoint {
  date: string; // MM-DD
  value: number;
}

export const getDashboardActivitiesApi = (limit = 10) => {
  return http.request<{ items: DashboardActivity[] }>("get", "/api/manager/dashboard/activities", {
    params: { limit }
  });
};

export const getDashboardGroupApi = () => {
  return http.request<GroupOverview>("get", "/api/manager/dashboard/group");
};

export const getDashboardHealthApi = () => {
  return http.request<{ items: HealthItem[] }>("get", "/api/manager/dashboard/health");
};

export const getDashboardResourcesApi = () => {
  return http.request<PlatformResources>("get", "/api/manager/dashboard/resources");
};

export const getDashboardBillingApi = () => {
  return http.request<BillingOverview>("get", "/api/manager/dashboard/billing");
};

export const getDashboardInstanceStatusApi = () => {
  return http.request<{ items: InstanceStatusItem[] }>("get", "/api/manager/dashboard/instance-status");
};

export const getDashboardTopAgentsApi = (limit = 5) => {
  return http.request<{ items: TopAgentItem[] }>("get", "/api/manager/dashboard/top-agents", {
    params: { limit }
  });
};

/** 终端用户最近 N 天对话次数趋势（数据源：Langfuse traces 按 metadata.enduser_id 过滤） */
export const getDashboardMyConversationTrendApi = (days = 7) => {
  return http.request<{ items: ConversationTrendPoint[] }>(
    "get",
    "/api/manager/dashboard/my-conversation-trend",
    { params: { days } }
  );
};

/** 终端用户首页统计：可访问 Agent 数 + 本月对话次数 */
export interface MyStats {
  accessible_agents: number;
  monthly_conversations: number;
}

export const getDashboardMyStatsApi = () => {
  return http.request<MyStats>("get", "/api/manager/dashboard/my-stats");
};
