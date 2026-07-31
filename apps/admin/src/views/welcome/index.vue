<script setup lang="ts">
import { ref, computed, watch, nextTick, onMounted } from "vue";
import { useI18n } from "vue-i18n";
import ReCol from "@/components/ReCol";
import { ReNormalCountTo } from "@/components/ReCountTo";
import DocsLink from "@/components/DocsLink/index.vue";
import { useDark, useECharts } from "@pureadmin/utils";
import { useUserStoreHook } from "@/store/modules/user";
import { getInstancesApi } from "@/api/manager/agentInstances";
import type { AgentInstanceResponse } from "@/api/manager/agentInstances";
import { getResourcePoolsApi } from "@/api/manager/resourcePools";
import { getUsersApi } from "@/api/manager/users";
import {
  getDashboardGroupApi,
  getDashboardHealthApi,
  getDashboardResourcesApi,
  getDashboardBillingApi,
  getDashboardInstanceStatusApi,
  getDashboardMyConversationTrendApi,
  getDashboardMyStatsApi,
  type GroupOverview,
  type HealthItem,
  type PlatformResources,
  type BillingOverview,
  type InstanceStatusItem,
  type ConversationTrendPoint,
  type MyStats
} from "@/api/manager/dashboard";
import {
  type TopAgentItem,
  getTopAgentsApi,
  getOperationLogsApi,
  type OperationLogItem
} from "@/api/manager/observability";
import dayjs from "dayjs";
import { useRouter } from "vue-router";
import Robot2Line from "~icons/ri/robot-2-line";
import TeamLine from "~icons/ri/team-line";
import Chat1Line from "~icons/ri/chat-1-line";
import Database2Line from "~icons/ri/database-2-line";
import RightArrow from "~icons/ri/arrow-right-s-line";
import AddLine from "~icons/ri/add-line";
import Key2Line from "~icons/ri/key-2-line";
import UserLine from "~icons/ri/user-line";
import Wallet3Line from "~icons/ri/wallet-3-line";
import Book2Line from "~icons/ri/book-2-line";

defineOptions({ name: "Welcome" });

const { t, te } = useI18n();
const { isDark } = useDark();
const theme = computed(() => (isDark.value ? "dark" : "light"));
const router = useRouter();
const userStore = useUserStoreHook();

// ── 角色判断 ──
const isSystemAdmin = computed(() => userStore.roles?.includes("系统管理员"));
const isGroupAdmin = computed(() => userStore.roles?.includes("运维人员"));
const isEndUser = computed(() => userStore.roles?.includes("终端用户"));

// ── 系统管理员：实时统计数据 ──
const allAgents = ref<AgentInstanceResponse[]>([]);
const realAgentCount = computed(() => allAgents.value.length);
const realUserCount = ref(0);
const realPoolCount = ref(0);
const agentWeeklyChange = ref(0);
const realPublishedCount = computed(() => allAgents.value.filter(a => a.status === "PUBLISHED").length);
const realStatusDist = ref<{ name: string; value: number; color: string }[]>([]);
const realEngineDist = ref<{ name: string; value: number; color: string }[]>([]);

// 热门 Agent Top5（近 30 天对话次数，来自 /dashboard/top-agents）
const topAgentsData = ref<TopAgentItem[]>([]);
const topAgents = computed(() => topAgentsData.value);

const pieChartRef = ref<HTMLDivElement>();
const { setOptions: setPieOpts } = useECharts(pieChartRef, { theme });

const engineChartRef = ref<HTMLDivElement>();
const { setOptions: setEngineOpts } = useECharts(engineChartRef, { theme });

const instanceChartRef = ref<HTMLDivElement>();
const { setOptions: setInstanceOpts } = useECharts(instanceChartRef, { theme });

// ── 引擎实例状态分布 ──
const instanceStatus = ref<InstanceStatusItem[]>([]);

// ── 系统健康 ──
const healthItems = ref<HealthItem[]>([]);

// ── 全平台资源实时用量 ──
const resources = ref<PlatformResources>({
  cpuUsed: 0,
  cpuLimit: 0,
  memUsed: 0,
  memLimit: 0,
  podCount: 0,
  metricsAvailable: false
});
const hasResources = computed(() => resources.value.podCount > 0);
const cpuPct = computed(() =>
  resources.value.cpuLimit
    ? Math.min(100, Math.round((resources.value.cpuUsed / resources.value.cpuLimit) * 100))
    : 0
);
const memPct = computed(() =>
  resources.value.memLimit
    ? Math.min(100, Math.round((resources.value.memUsed / resources.value.memLimit) * 100))
    : 0
);

// ── Token / 计费 ──
const billing = ref<BillingOverview>({ todayTokens: 0, monthlyTokens: 0, monthlyCost: 0 });
const billingUnreachable = ref(false);

function fmtCpu(m: number): string {
  return m >= 1000 ? (m / 1000).toFixed(1) + " 核" : m + " m";
}
function fmtMem(mi: number): string {
  return mi >= 1024 ? (mi / 1024).toFixed(1) + " Gi" : mi + " Mi";
}
function fmtTokens(n: number): string {
  if (n >= 1e8) return (n / 1e8).toFixed(2) + " 亿";
  if (n >= 1e4) return (n / 1e4).toFixed(1) + " 万";
  return n.toLocaleString();
}
function fmtCost(n: number): string {
  return "¥" + n.toLocaleString(undefined, { maximumFractionDigits: 2 });
}

// ── 活动动态颜色（图例与时间线共用） ──
function activityColor(type: string): string {
  return type === "publish"
    ? "#00a870"
    : type === "create"
      ? "#386bf5"
      : type === "offline"
        ? "#f56c6c"
        : "#909399";
}

// operation-log action → 时间线 type（与图例 4 色对齐）
function mapActionToType(action: string): "publish" | "create" | "edit" | "offline" {
  if (action === "agent_instance.publish") return "publish";
  if (action === "agent_instance.create" || action === "agent_instance.clone") return "create";
  if (
    action === "agent_instance.offline" ||
    action === "agent_instance.delete" ||
    action === "agent_instance.destroy"
  )
    return "offline";
  return "edit";
}

function actorLabel(item: OperationLogItem): string {
  if (item.actor_real_name) return item.actor_real_name;
  if (item.actor_name) return item.actor_name;
  return t("operationLog.targetDeleted");
}

function actionVerbLabel(action: string): string {
  const verb = action.split(".")[1] || "";
  const key = `operationLog.actionVerb.${verb}`;
  return te(key) ? t(key) : verb;
}

function targetLabel(item: OperationLogItem): string {
  if (item.target_name) return item.target_name;
  if (item.target_id) return item.target_id.slice(0, 8) + "…";
  return "—";
}

function renderPieCharts() {
  if (!pieChartRef.value || !engineChartRef.value) return;
  nextTick(() => {
    const baseLegend = { bottom: 0, type: "scroll", itemGap: 14, textStyle: { fontSize: 11 } };
    const mk = (data: { name: string; value: number; color: string }[]) => ({
      tooltip: { trigger: "item" },
      legend: baseLegend,
      series: [
        {
          type: "pie",
          radius: ["38%", "58%"],
          center: ["50%", "42%"],
          avoidLabelOverlap: true,
          itemStyle: { borderRadius: 6, borderColor: "transparent" },
          label: { show: false },
          emphasis: { label: { show: true, fontSize: 12, fontWeight: "bold" } },
          data: data.map(d => ({ value: d.value, name: d.name, itemStyle: { color: d.color } }))
        }
      ]
    });
    setPieOpts(mk(realStatusDist.value) as any);
    setEngineOpts(mk(realEngineDist.value) as any);
    if (instanceChartRef.value) setInstanceOpts(mk(instanceStatus.value) as any);
  });
}

const statusColors: Record<string, string> = {
  PUBLISHED: "#00a870",
  DRAFT: "#f59e0b",
  OFFLINE: "#909399"
};
const statusLabels = computed<Record<string, string>>(() => ({
  PUBLISHED: t("common.status.published"),
  DRAFT: t("common.status.draft"),
  OFFLINE: t("common.status.offline")
}));
const engineColors: Record<string, string> = { HERMES: "#386bf5", OPENCLAW: "#e6a23c" };

async function fetchAdminStats() {
  try {
    const [agentsRes, usersRes, engineRes] = await Promise.all([
      getInstancesApi({ page: 1, page_size: 100 }),
      getUsersApi({ page: 1, page_size: 1 }),
      getResourcePoolsApi({ page: 1, page_size: 100 }).catch(() => ({ items: [], total: 0 }))
    ]);
    allAgents.value = agentsRes.items || [];
    realUserCount.value = usersRes.total || 0;
    realPoolCount.value = (engineRes as any).items?.length || (engineRes as any).total || 0;

    // 热门实例 Top5（独立请求，失败不阻塞首页）
    getTopAgentsApi({ limit: 5 })
      .then(res => (topAgentsData.value = res.items || []))
      .catch(() => (topAgentsData.value = []));

    // 计算本周新增实例
    const weekAgo = dayjs().subtract(7, "day");
    agentWeeklyChange.value = allAgents.value.filter(a => dayjs(a.created_at).isAfter(weekAgo)).length;

    // 统计状态分布
    const statusMap: Record<string, number> = {};
    const engineMap: Record<string, number> = {};
    allAgents.value.forEach((a: AgentInstanceResponse) => {
      statusMap[a.status] = (statusMap[a.status] || 0) + 1;
      // V3: engine_type 直接来自实例（定义层枚举）
      const et = (a.engine_type || "HERMES") as string;
      engineMap[et] = (engineMap[et] || 0) + 1;
    });
    realStatusDist.value = Object.entries(statusMap).map(([k, v]) => ({
      name: statusLabels.value[k] || k,
      value: v,
      color: statusColors[k] || "#909399"
    }));
    realEngineDist.value = Object.entries(engineMap).map(([k, v]) => ({
      name: k,
      value: v,
      color: engineColors[k] || "#909399"
    }));
    renderPieCharts();
  } catch {
    // 静默失败
  }
}

async function fetchInstanceStatus() {
  try {
    const res = await getDashboardInstanceStatusApi();
    instanceStatus.value = res.items || [];
    renderPieCharts();
  } catch {
    instanceStatus.value = [];
  }
}

async function fetchHealth() {
  try {
    const res = await getDashboardHealthApi();
    healthItems.value = res.items || [];
  } catch {
    healthItems.value = [];
  }
}

async function fetchResources() {
  try {
    resources.value = await getDashboardResourcesApi();
  } catch {
    resources.value = { cpuUsed: 0, cpuLimit: 0, memUsed: 0, memLimit: 0, podCount: 0, metricsAvailable: false };
  }
}

async function fetchBilling() {
  try {
    billing.value = await getDashboardBillingApi();
    billingUnreachable.value = false;
  } catch {
    billingUnreachable.value = true;
  }
}

watch(isSystemAdmin, val => {
  if (val) {
    fetchAdminStats();
    fetchInstanceStatus();
    fetchHealth();
    fetchResources();
    fetchBilling();
    fetchActivities();
  }
});
onMounted(() => {
  if (isSystemAdmin.value) {
    fetchAdminStats();
    fetchInstanceStatus();
    fetchHealth();
    fetchResources();
    fetchBilling();
    fetchActivities();
  }
});

// ── 最近活动动态（来自 operation-logs 审计表，过滤智能体生命周期成功操作） ──
const realActivities = ref<OperationLogItem[]>([]);

function formatActivityTime(iso: string): string {
  const t = dayjs(iso);
  const now = dayjs();
  if (t.isAfter(now.subtract(1, "minute"))) return "刚刚";
  if (t.isSame(now, "day")) return t.format("HH:mm");
  if (t.isAfter(now.subtract(1, "day"))) return "昨天 " + t.format("HH:mm");
  return t.format("MM-DD HH:mm");
}

async function fetchActivities() {
  try {
    const thirtyDaysAgo = dayjs().subtract(30, "day").toISOString();
    const resp = await getOperationLogsApi({
      target_type: "agent_instance",
      status: "success",
      time_from: thirtyDaysAgo,
      pageSize: 8,
      currentPage: 1
    });
    realActivities.value = resp.data?.list || [];
  } catch {
    realActivities.value = [];
  }
}

// ── 快捷入口 ──
const quickEntries = computed(() => [
  { icon: AddLine, label: t("welcome.quickEntry.createAgent"), route: "/agent-instances/index", color: "#386bf5" },
  { icon: Database2Line, label: t("welcome.quickEntry.enginePool"), route: "/resource-pools/index", color: "#00a870" },
  { icon: Key2Line, label: t("welcome.quickEntry.modelGateway"), route: "/litellm/keys", color: "#e6a23c" },
  { icon: UserLine, label: t("welcome.quickEntry.userMgmt"), route: "/system/user/index", color: "#9b59b6" },
  { icon: Wallet3Line, label: t("welcome.quickEntry.billing"), route: "/monitoring/usage", color: "#f56c6c" },
  { icon: Book2Line, label: t("welcome.quickEntry.knowledge"), route: "/knowledge", color: "#0bb6a2" }
]);

// ── 用户组管理员概览（真实数据） ──
const groupOverview = ref<GroupOverview>({
  groupName: "",
  agentCount: 0,
  memberCount: 0,
  todayConversations: 0,
  monthlyTokens: 0,
  agentDistribution: []
});
const groupDistMax = computed(() => Math.max(1, groupOverview.value.agentCount));

async function fetchGroupOverview() {
  try {
    groupOverview.value = await getDashboardGroupApi();
  } catch {
    // 静默失败，保持 0 值
  }
}

watch(isGroupAdmin, val => {
  if (val) fetchGroupOverview();
}, { immediate: true });

// ── 个人对话趋势（真实数据，来自 Langfuse traces 按 enduser_id 过滤） ──
const trendChartRef = ref<HTMLDivElement>();
const { setOptions: setTrendOpts } = useECharts(trendChartRef, { theme });
const conversationTrend = ref<ConversationTrendPoint[]>([]);

async function fetchConversationTrend() {
  try {
    const res = await getDashboardMyConversationTrendApi(7);
    conversationTrend.value = res.items || [];
  } catch {
    conversationTrend.value = [];
  }
}

function renderTrendChart() {
  if (!trendChartRef.value) return;
  nextTick(() => {
    setTrendOpts({
      tooltip: { trigger: "axis" },
      grid: { top: 15, left: 35, right: 10, bottom: 25 },
      xAxis: {
        type: "category",
        data: conversationTrend.value.map(d => d.date),
        axisLabel: { fontSize: "0.7rem" },
        axisLine: { show: false }
      },
      yAxis: {
        type: "value",
        splitLine: { lineStyle: { type: "dashed", opacity: 0.2 } },
        axisLabel: { fontSize: "0.7rem" }
      },
      series: [
        {
          type: "bar",
          data: conversationTrend.value.map(d => d.value),
          itemStyle: { color: "#41b6ff", borderRadius: [4, 4, 0, 0] },
          barWidth: 20
        }
      ]
    });
  });
}

// DOM 挂载或数据更新时重渲染
watch(trendChartRef, () => renderTrendChart(), { immediate: true });
watch(conversationTrend, () => renderTrendChart());

// ── 我的 Agent（真实数据） ──
const realAgents = ref<AgentInstanceResponse[]>([]);
const agentsLoading = ref(false);

async function fetchMyAgents() {
  agentsLoading.value = true;
  try {
    const res = await getInstancesApi({ page: 1, page_size: 20 });
    realAgents.value = (res.items || []).filter(a => a.status === "PUBLISHED");
  } catch {
    // 静默失败，保持空列表
  } finally {
    agentsLoading.value = false;
  }
}

// ── 我的统计（真实数据：可访问 Agent 数 + 本月对话次数） ──
const myStats = ref<MyStats>({ accessible_agents: 0, monthly_conversations: 0 });

async function fetchMyStats() {
  try {
    myStats.value = await getDashboardMyStatsApi();
  } catch {
    // 静默失败，保持 0
  }
}

// 如果是 endUser 则加载
watch(isEndUser, val => {
  if (val) {
    fetchMyAgents();
    fetchConversationTrend();
    fetchMyStats();
  }
}, { immediate: true });

function goToAgentList() {
  router.push("/agent-instances/index");
}

function goToAgentDetail(id: string) {
  router.push(`/agent-instances/detail/${id}`);
}
</script>

<template>
  <div class="main">
    <DocsLink to="welcome.html" />
    <div class="welcome">
      <!-- ====== 系统管理员视图 ====== -->
      <template v-if="isSystemAdmin">
        <el-row :gutter="16" class="equal-height" style="margin-bottom: 0">
          <!-- ====== 左侧：概览区域（~73%） ====== -->
          <el-col :xs="24" :md="17" style="margin-bottom: 0; display: flex; flex-direction: column">
            <!-- 平台概览数字卡片 -->
            <el-row :gutter="12" class="mb-2">
              <re-col :value="6" :xs="12" :sm="6" class="mb-2">
                <el-card shadow="never" class="stat-card">
                  <div class="stat-header">
                    <span class="stat-label">{{ t("welcome.stat.agentTotal") }}</span>
                    <div class="stat-icon" style="background: #41b6ff18; color: #41b6ff">
                      <Robot2Line width="16" height="16" />
                    </div>
                  </div>
                  <ReNormalCountTo :endVal="realAgentCount" fontSize="1.4em" separator="," />
                  <p class="stat-change" :style="{ color: agentWeeklyChange > 0 ? '#00a870' : '#909399' }">
                    {{ agentWeeklyChange > 0 ? "+" + agentWeeklyChange : "0" }}
                    {{ t("welcome.stat.weeklyNew") }}
                  </p>
                </el-card>
              </re-col>
              <re-col :value="6" :xs="12" :sm="6" class="mb-2">
                <el-card shadow="never" class="stat-card">
                  <div class="stat-header">
                    <span class="stat-label">{{ t("welcome.stat.userTotal") }}</span>
                    <div class="stat-icon" style="background: #00a87018; color: #00a870">
                      <TeamLine width="16" height="16" />
                    </div>
                  </div>
                  <ReNormalCountTo :endVal="realUserCount" fontSize="1.4em" separator="," />
                  <p class="stat-change" style="color: #909399">{{ t("welcome.stat.systemUser") }}</p>
                </el-card>
              </re-col>
              <re-col :value="6" :xs="12" :sm="6" class="mb-2">
                <el-card shadow="never" class="stat-card">
                  <div class="stat-header">
                    <span class="stat-label">{{ t("welcome.stat.publishedAgent") }}</span>
                    <div class="stat-icon" style="background: #f59e0b18; color: #f59e0b">
                      <Chat1Line width="16" height="16" />
                    </div>
                  </div>
                  <ReNormalCountTo :endVal="realPublishedCount" fontSize="1.4em" separator="," />
                  <p class="stat-change" style="color: #909399">
                    {{
                      t("welcome.stat.publishRate", {
                        rate: realAgentCount > 0 ? Math.round((realPublishedCount / realAgentCount) * 100) : 0
                      })
                    }}
                  </p>
                </el-card>
              </re-col>
              <re-col :value="6" :xs="12" :sm="6" class="mb-2">
                <el-card shadow="never" class="stat-card">
                  <div class="stat-header">
                    <span class="stat-label">{{ t("welcome.stat.poolStock") }}</span>
                    <div class="stat-icon" style="background: #9b59b618; color: #9b59b6">
                      <Database2Line width="16" height="16" />
                    </div>
                  </div>
                  <ReNormalCountTo :endVal="realPoolCount" fontSize="1.4em" separator="," />
                  <p class="stat-change" style="color: #909399">{{ t("welcome.stat.engineTemplate") }}</p>
                </el-card>
              </re-col>
            </el-row>

            <!-- 系统健康 + 告警 -->
            <el-row :gutter="12" class="mb-2">
              <el-col :span="24">
                <el-card shadow="never" class="health-bar">
                  <div class="flex items-center gap-4 flex-wrap">
                    <span class="text-xs text-gray-400 font-medium">{{ t("welcome.health") }}</span>
                    <template v-if="healthItems.length">
                      <span v-for="h in healthItems" :key="h.name" class="health-dot">
                        <span
                          class="health-dot-circle"
                          :style="{ background: h.status === 'ok' ? '#00a870' : h.status === 'undeployed' ? '#c0c4cc' : '#f56c6c' }"
                        />
                        {{ h.name }}
                        <span v-if="h.status === 'down'" class="health-down">{{ t("welcome.healthDown") }}</span>
                        <span v-else-if="h.status === 'undeployed'" class="health-undeployed">{{ t("welcome.healthUndeployed") }}</span>
                      </span>
                    </template>
                    <span v-else class="text-xs text-gray-300">{{ t("welcome.healthUnknown") }}</span>
                    <div class="flex items-center gap-2 ml-auto">
                      <span class="text-xs text-gray-300">{{ t("welcome.healthHint") }}</span>
                      <el-button text size="small" @click="router.push('/monitoring/service-health')">查看更多</el-button>
                    </div>
                  </div>
                </el-card>
              </el-col>
            </el-row>

            <!-- 图表行：Agent 分布 + 引擎分布 + 引擎实例状态 -->
            <el-row :gutter="12" class="equal-height" style="flex: 1">
              <el-col :xs="24" :sm="8" class="mb-0">
                <el-card shadow="never" class="h-full chart-card">
                  <template #header>
                    <span class="card-title" style="font-size: 13px">{{ t("welcome.chart.agentStatus") }}</span>
                  </template>
                  <div ref="pieChartRef" class="chart-fill" />
                </el-card>
              </el-col>
              <el-col :xs="24" :sm="8" class="mb-0">
                <el-card shadow="never" class="h-full chart-card">
                  <template #header>
                    <span class="card-title" style="font-size: 13px">{{ t("welcome.chart.engineDist") }}</span>
                  </template>
                  <div ref="engineChartRef" class="chart-fill" />
                </el-card>
              </el-col>
              <el-col :xs="24" :sm="8" class="mb-0">
                <el-card shadow="never" class="h-full chart-card">
                  <template #header>
                    <span class="card-title" style="font-size: 13px">{{ t("welcome.chart.instanceStatus") }}</span>
                  </template>
                  <div ref="instanceChartRef" class="chart-fill" />
                </el-card>
              </el-col>
            </el-row>
          </el-col>

          <!-- ====== 右侧：快捷入口（~27%） ====== -->
          <el-col :xs="24" :md="7" style="margin-bottom: 0">
            <el-card shadow="never" class="h-full quick-card">
              <template #header>
                <span class="card-title" style="font-size: 13px">{{ t("welcome.chart.quickEntry") }}</span>
              </template>
              <div class="quick-grid">
                <div
                  v-for="q in quickEntries"
                  :key="q.route"
                  class="quick-item"
                  @click="router.push(q.route)"
                >
                  <div class="quick-icon" :style="{ background: q.color + '18', color: q.color }">
                    <component :is="q.icon" width="22" height="22" />
                  </div>
                  <span class="quick-label">{{ q.label }}</span>
                </div>
              </div>
            </el-card>
          </el-col>
        </el-row>

        <!-- ====== 底部行：资源配额 + Token/计费 + 热门 Agent + 快捷入口 ====== -->
        <el-row :gutter="12" class="dash-bottom-row">
          <!-- 全平台资源配额 -->
          <el-col :xs="24" :sm="12" :md="6" class="mb-0">
            <el-card shadow="never" class="h-full">
              <template #header>
                <div class="flex items-center justify-between">
                  <span class="card-title" style="font-size: 13px">{{ t("welcome.chart.resource") }}</span>
                  <div class="flex items-center gap-2">
                    <el-button text size="small" @click="router.push('/monitoring/resources')">查看资源消耗</el-button>
                    <el-tag size="small" :type="resources.metricsAvailable ? 'success' : 'info'" effect="plain">{{
                      resources.metricsAvailable ? t("welcome.chart.realtime") : t("welcome.chart.allocated")
                    }}</el-tag>
                  </div>
                </div>
              </template>
              <div v-if="hasResources" class="resource-bars">
                <div class="res-pod-stat">
                  <span class="res-pod-num">{{ resources.podCount }}</span>
                  <span class="res-pod-label">{{ t("welcome.resource.pods") }}</span>
                </div>
                <div class="res-row">
                  <span class="res-label">{{ t("welcome.resource.cpu") }}</span>
                  <el-progress :percentage="cpuPct" :stroke-width="14" color="#41b6ff" :show-text="false" />
                  <span class="res-val">{{ cpuPct }}%</span>
                </div>
                <p class="res-sub">
                  {{ t("welcome.resource.used", { used: fmtCpu(resources.cpuUsed), limit: fmtCpu(resources.cpuLimit) }) }}
                </p>
                <div class="res-row">
                  <span class="res-label">{{ t("welcome.resource.memory") }}</span>
                  <el-progress :percentage="memPct" :stroke-width="14" color="#00a870" :show-text="false" />
                  <span class="res-val">{{ memPct }}%</span>
                </div>
                <p class="res-sub">
                  {{ t("welcome.resource.used", { used: fmtMem(resources.memUsed), limit: fmtMem(resources.memLimit) }) }}
                </p>
              </div>
              <el-empty v-else :description="t('welcome.resource.noData')" :image-size="50" />
            </el-card>
          </el-col>

          <!-- Token / 计费概览 -->
          <el-col :xs="24" :sm="12" :md="6" class="mb-0">
            <el-card shadow="never" class="h-full">
              <template #header>
                <div class="flex items-center justify-between">
                  <span class="card-title" style="font-size: 13px">{{ t("welcome.chart.billing") }}</span>
                  <div class="flex items-center gap-2">
                    <el-button text size="small" @click="router.push('/monitoring/usage')">查看更多</el-button>
                    <el-tag v-if="billingUnreachable" size="small" type="danger" effect="plain">{{
                      t("welcome.billing.unreachable")
                    }}</el-tag>
                  </div>
                </div>
              </template>
              <div class="billing-list">
                <div class="billing-row">
                  <span class="billing-label">{{ t("welcome.billing.todayTokens") }}</span>
                  <span class="billing-val">{{ fmtTokens(billing.todayTokens) }}</span>
                </div>
                <div class="billing-row">
                  <span class="billing-label">{{ t("welcome.billing.monthlyTokens") }}</span>
                  <span class="billing-val">{{ fmtTokens(billing.monthlyTokens) }}</span>
                </div>
                <div class="billing-row">
                  <span class="billing-label">{{ t("welcome.billing.monthlyCost") }}</span>
                  <span class="billing-val billing-cost">{{ fmtCost(billing.monthlyCost) }}</span>
                </div>
              </div>
            </el-card>
          </el-col>

          <!-- 热门 Agent Top5 -->
          <el-col :xs="24" :sm="12" :md="6" class="mb-0">
            <el-card shadow="never" class="h-full">
              <template #header>
                <div class="flex items-center justify-between">
                  <span class="card-title" style="font-size: 13px">{{ t("welcome.chart.topAgents") }}</span>
                  <el-button text size="small" @click="router.push('/agent-instances')">查看更多</el-button>
                </div>
              </template>
              <div class="top-agents">
                <div
                  v-for="(a, i) in topAgents"
                  :key="a.agent_id"
                  class="top-agent-item"
                  :title="`对话次数：${a.conversation_count || 0}`"
                  @click="goToAgentDetail(a.agent_id)"
                >
                  <span class="top-rank" :class="{ 'top-rank-lead': i < 3 }">{{ i + 1 }}</span>
                  <span class="agent-mini-dot" :style="{ background: '#386bf5' }" />
                  <div class="agent-mini-info">
                    <p class="agent-mini-name">{{ a.name }}</p>
                  </div>
                  <span class="top-count">{{ a.conversation_count || 0 }}</span>
                </div>
                <el-empty v-if="!topAgents.length" :image-size="50" />
              </div>
            </el-card>
          </el-col>

          <!-- 最近操作动态 -->
          <el-col :xs="24" :sm="12" :md="6" class="mb-0">
            <el-card shadow="never" class="h-full">
              <template #header>
                <div class="flex items-center justify-between activity-header">
                  <span class="card-title" style="font-size: 13px">{{ t("welcome.chart.recentActivity") }}</span>
                  <div class="activity-legend">
                    <span v-for="lk in ['publish', 'create', 'offline', 'edit']" :key="lk" class="activity-legend-item">
                      <span class="health-dot-circle" :style="{ background: activityColor(lk) }" />
                      {{ t("welcome.activity.legend." + lk) }}
                    </span>
                  </div>
                </div>
              </template>
              <el-scrollbar max-height="300px">
                <el-timeline v-if="realActivities.length">
                  <el-timeline-item
                    v-for="(act, i) in realActivities"
                    :key="i"
                    :timestamp="formatActivityTime(act.created_at || '')"
                    placement="top"
                    :color="activityColor(mapActionToType(act.action))"
                  >
                    <div class="activity-text">
                      <strong>{{ actorLabel(act) }}</strong> {{ actionVerbLabel(act.action) }}了 智能体
                      <el-link type="primary" underline="never" class="ml-1">{{ targetLabel(act) }}</el-link>
                    </div>
                  </el-timeline-item>
                </el-timeline>
                <el-empty v-else :description="t('welcome.chart.sampleData')" :image-size="50" />
              </el-scrollbar>
            </el-card>
          </el-col>
        </el-row>
      </template>

      <!-- ====== 用户组管理员视图 ====== -->
      <template v-else-if="isGroupAdmin">
        <el-row :gutter="16" class="mb-4">
          <re-col :value="8" :xs="12" :sm="8" class="mb-3">
            <el-card shadow="never" class="stat-card">
              <div class="stat-header">
                <span class="stat-label">{{ t("welcome.stat.groupAgent") }}</span>
                <div class="stat-icon" style="background: #386bf518; color: #386bf5">
                  <Robot2Line width="18" height="18" />
                </div>
              </div>
              <ReNormalCountTo :endVal="groupOverview.agentCount" fontSize="1.6em" />
              <p class="stat-change" style="color: #909399">
                {{ t("welcome.groupPrefix", { name: groupOverview.groupName || "—" }) }}
              </p>
            </el-card>
          </re-col>
          <re-col :value="8" :xs="12" :sm="8" class="mb-3">
            <el-card shadow="never" class="stat-card">
              <div class="stat-header">
                <span class="stat-label">{{ t("welcome.stat.groupMember") }}</span>
                <div class="stat-icon" style="background: #00a87018; color: #00a870">
                  <TeamLine width="18" height="18" />
                </div>
              </div>
              <ReNormalCountTo :endVal="groupOverview.memberCount" fontSize="1.6em" />
            </el-card>
          </re-col>
          <re-col :value="8" :xs="24" :sm="8" class="mb-3">
            <el-card shadow="never" class="stat-card">
              <div class="stat-header">
                <span class="stat-label">{{ t("welcome.stat.todayConversation") }}</span>
                <div class="stat-icon" style="background: #f59e0b18; color: #f59e0b">
                  <Chat1Line width="18" height="18" />
                </div>
              </div>
              <ReNormalCountTo :endVal="groupOverview.todayConversations" fontSize="1.6em" separator="," />
            </el-card>
          </re-col>
        </el-row>

        <el-row :gutter="16" class="mb-4">
          <el-col :xs="24" :sm="12" class="mb-3">
            <el-card shadow="never">
              <template #header><span class="card-title">{{ t("welcome.chart.agentStatus") }}</span></template>
              <div class="bar-list">
                <div v-for="d in groupOverview.agentDistribution" :key="d.name" class="bar-item">
                  <span class="bar-label">{{ d.name }}</span>
                  <el-progress :percentage="(d.value / groupDistMax) * 100" :stroke-width="16" :color="d.color" />
                  <span class="bar-value">{{ d.value }}</span>
                </div>
              </div>
            </el-card>
          </el-col>
          <el-col :xs="24" :sm="12" class="mb-3">
            <el-card shadow="never">
              <template #header><span class="card-title">{{ t("welcome.chart.quickEntry") }}</span></template>
              <div class="quick-links">
                <el-button text @click="goToAgentList">
                  <Robot2Line width="16" height="16" class="mr-1" style="vertical-align: middle" />
                  {{ t("welcome.action.manageGroupAgent") }}
                  <RightArrow width="14" height="14" class="ml-1" />
                </el-button>
              </div>
            </el-card>
          </el-col>
        </el-row>
      </template>

      <!-- ====== 普通用户视图 ====== -->
      <template v-else>
        <el-row :gutter="16" class="mb-4">
          <re-col :value="12" :xs="12" :sm="12" class="mb-3">
            <el-card shadow="never" class="stat-card">
              <div class="stat-header">
                <span class="stat-label">{{ t("welcome.stat.accessibleAgent") }}</span>
                <div class="stat-icon" style="background: #386bf518; color: #386bf5">
                  <Robot2Line width="18" height="18" />
                </div>
              </div>
              <ReNormalCountTo :endVal="myStats.accessible_agents" fontSize="1.6em" />
            </el-card>
          </re-col>
          <re-col :value="12" :xs="12" :sm="12" class="mb-3">
            <el-card shadow="never" class="stat-card">
              <div class="stat-header">
                <span class="stat-label">{{ t("welcome.stat.monthlyConversation") }}</span>
                <div class="stat-icon" style="background: #f59e0b18; color: #f59e0b">
                  <Chat1Line width="18" height="18" />
                </div>
              </div>
              <ReNormalCountTo :endVal="myStats.monthly_conversations" fontSize="1.6em" separator="," />
            </el-card>
          </re-col>
        </el-row>

        <el-row :gutter="16" class="mb-4">
          <!-- 我的 Agent -->
          <el-col :xs="24" :sm="14" class="mb-3">
            <el-card shadow="never">
              <template #header>
                <div class="flex items-center justify-between">
                  <span class="card-title">{{ t("welcome.chart.myAgent") }}</span>
                  <el-button text size="small" @click="goToAgentList">{{ t("welcome.chart.viewAll") }}</el-button>
                </div>
              </template>
              <div class="agent-grid">
                <div
                  v-for="agent in realAgents"
                  :key="agent.id"
                  class="agent-mini-card"
                  @click="goToAgentDetail(agent.id)"
                >
                  <div
                    class="agent-mini-dot"
                    :style="{
                      background: (agent.resource_pool_name || '').toLowerCase().includes('hermes')
                        ? '#386bf5'
                        : '#e6a23c'
                    }"
                  />
                  <div class="agent-mini-info">
                    <p class="agent-mini-name">{{ agent.name }}</p>
                    <p class="agent-mini-desc">{{ agent.description }}</p>
                  </div>
                  <span class="agent-mini-count">{{ (agent as any).conversation_count || 0 }}</span>
                </div>
              </div>
            </el-card>
          </el-col>
          <!-- 对话趋势 -->
          <el-col :xs="24" :sm="10" class="mb-3">
            <el-card shadow="never">
              <template #header><span class="card-title">{{ t("welcome.chart.trend7d") }}</span></template>
              <div ref="trendChartRef" style="width: 100%; height: 200px" />
            </el-card>
          </el-col>
        </el-row>
      </template>
    </div>
  </div>
</template>

<style scoped>
.welcome {
  max-width: 1400px;
  margin: 0 auto;
  min-height: calc(100vh - 140px);
  display: flex;
  flex-direction: column;
}

.equal-height {
  display: flex;
  flex-wrap: wrap;
}

.dash-bottom-row {
  flex: 1;
  display: flex;
  flex-wrap: wrap;
  margin-top: 12px;
}

.h-full {
  height: 100%;
}

.chart-card {
  display: flex;
  flex-direction: column;
}

.chart-card :deep(.el-card__body) {
  flex: 1;
  display: flex;
  flex-direction: column;
  padding: 12px;
}

.chart-fill {
  flex: 1;
  width: 100%;
  min-height: 170px;
}

.stat-card {
  border-radius: 8px;
}

.stat-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin-bottom: 8px;
}

.stat-label {
  font-size: 13px;
  color: var(--el-text-color-secondary);
}

.stat-icon {
  display: flex;
  align-items: center;
  justify-content: center;
  width: 32px;
  height: 32px;
  border-radius: 8px;
}

.stat-change {
  margin: 4px 0 0;
  font-size: 12px;
  font-weight: 500;
}

.card-title {
  font-size: 14px;
  font-weight: 600;
}

/* 系统健康条 */
.health-bar {
  border-radius: 8px;
}
.health-bar :deep(.el-card__body) {
  padding: 8px 16px;
}
.health-dot {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  font-size: 12px;
  color: var(--el-text-color-secondary);
}
.health-dot-circle {
  display: inline-block;
  width: 7px;
  height: 7px;
  border-radius: 50%;
}
.health-down {
  margin-left: 2px;
  font-size: 11px;
  color: #f56c6c;
}
.health-undeployed {
  margin-left: 2px;
  font-size: 11px;
  color: #909399;
}

/* 活动动态 */
.activity-header {
  gap: 8px;
}
.activity-legend {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
}
.activity-legend-item {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  font-size: 11px;
  color: var(--el-text-color-secondary);
}
.activity-text {
  font-size: 13px;
}

/* 资源用量条 */
.resource-bars {
  display: flex;
  flex-direction: column;
  gap: 2px;
}
.res-row {
  display: flex;
  align-items: center;
  gap: 8px;
}
.res-label {
  width: 36px;
  flex-shrink: 0;
  font-size: 12px;
  color: var(--el-text-color-secondary);
}
.res-row :deep(.el-progress) {
  flex: 1;
}
.res-val {
  width: 40px;
  text-align: right;
  font-size: 12px;
  font-weight: 600;
}
.res-sub {
  margin: 2px 0 8px 44px;
  font-size: 11px;
  color: var(--el-text-color-placeholder);
}
.res-pod-stat {
  display: flex;
  align-items: baseline;
  gap: 8px;
  padding: 8px 12px;
  margin-bottom: 12px;
  border-radius: 8px;
  background: var(--el-color-primary-light-9);
  border: 1px solid var(--el-color-primary-light-7);
}
.res-pod-num {
  font-size: 22px;
  font-weight: 700;
  line-height: 1;
  color: var(--el-color-primary);
}
.res-pod-label {
  font-size: 12px;
  color: var(--el-text-color-secondary);
}

/* Token / 计费 */
.billing-list {
  display: flex;
  flex-direction: column;
  gap: 14px;
  padding: 4px 0;
}
.billing-row {
  display: flex;
  align-items: baseline;
  justify-content: space-between;
}
.billing-label {
  font-size: 13px;
  color: var(--el-text-color-secondary);
}
.billing-val {
  font-size: 18px;
  font-weight: 600;
}
.billing-cost {
  color: #f56c6c;
}

/* 热门 Agent */
.top-agents {
  display: flex;
  flex-direction: column;
  gap: 4px;
}
.top-agent-item {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 8px 6px;
  border-radius: 6px;
  cursor: pointer;
  transition: background 0.2s;
}
.top-agent-item:hover {
  background: var(--el-fill-color-light);
}
.top-rank {
  width: 20px;
  height: 20px;
  flex-shrink: 0;
  display: flex;
  align-items: center;
  justify-content: center;
  border-radius: 50%;
  font-size: 11px;
  font-weight: 700;
  color: var(--el-text-color-secondary);
  background: var(--el-fill-color-light);
}
.top-rank-lead {
  color: #fff;
  background: #41b6ff;
}
.top-count {
  font-size: 13px;
  font-weight: 600;
  color: var(--el-text-color-primary);
  flex-shrink: 0;
}

/* 快捷入口 */
.quick-card :deep(.el-card__body) {
  flex: 1;
  display: flex;
  flex-direction: column;
  padding: 12px;
}
.quick-grid {
  flex: 1;
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 12px;
  padding: 2px 0;
  align-content: stretch;
}
.quick-item {
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  gap: 8px;
  padding: 14px 8px;
  border-radius: 10px;
  cursor: pointer;
  transition: background 0.2s;
}
.quick-item:hover {
  background: var(--el-fill-color-light);
}
.quick-icon {
  display: flex;
  align-items: center;
  justify-content: center;
  width: 40px;
  height: 40px;
  border-radius: 10px;
  flex-shrink: 0;
}
.quick-label {
  font-size: 13px;
  text-align: center;
  color: var(--el-text-color-primary);
}

/* 进度条列表 */
.bar-list {
  display: flex;
  flex-direction: column;
  gap: 16px;
  padding: 8px 0;
}

.bar-item {
  display: flex;
  align-items: center;
  gap: 10px;
}

.bar-label {
  font-size: 13px;
  width: 56px;
  flex-shrink: 0;
}

.bar-value {
  font-size: 14px;
  font-weight: 600;
  width: 24px;
  text-align: right;
}

/* 快速入口 */
.quick-links {
  display: flex;
  flex-direction: column;
  gap: 8px;
}

/* Agent 卡片网格 */
.agent-grid {
  display: flex;
  flex-direction: column;
  gap: 8px;
}

.agent-mini-card {
  display: flex;
  align-items: center;
  gap: 10px;
  padding: 10px 12px;
  border-radius: 6px;
  cursor: pointer;
  transition: background 0.2s;
}

.agent-mini-card:hover {
  background: var(--el-fill-color-light);
}

.agent-mini-dot {
  width: 8px;
  height: 8px;
  border-radius: 50%;
  flex-shrink: 0;
}

.agent-mini-info {
  flex: 1;
  min-width: 0;
}

.agent-mini-name {
  margin: 0;
  font-size: 13px;
  font-weight: 500;
}

.agent-mini-desc {
  margin: 2px 0 0;
  font-size: 11px;
  color: var(--el-text-color-secondary);
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}

.agent-mini-count {
  font-size: 12px;
  color: var(--el-text-color-placeholder);
  flex-shrink: 0;
}
</style>
