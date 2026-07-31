<script setup lang="ts">
import { ref, computed, watch, nextTick, onMounted } from "vue";
import { useI18n } from "vue-i18n";
import type {
  AgentInstanceResponse,
  DeploymentStatus,
  InstanceOverview
} from "@/api/manager/agentInstances";
import { getInstanceOverviewApi } from "@/api/manager/agentInstances";
import ReCol from "@/components/ReCol";
import { ReNormalCountTo } from "@/components/ReCountTo";
import { useDark, useECharts } from "@pureadmin/utils";
import dayjs from "dayjs";
import Chat1Line from "~icons/ri/chat-1-line";
import CoinLine from "~icons/ri/coin-line";
import User3Line from "~icons/ri/user-3-line";
import ServerLine from "~icons/ri/server-line";

defineOptions({ name: "InstanceOverviewTab" });

const props = defineProps<{
  instance: AgentInstanceResponse;
  deployStatus: DeploymentStatus | null;
}>();

const { t } = useI18n();
const { isDark } = useDark();
const theme = computed(() => (isDark.value ? "dark" : "light"));

// ── Dify 外部对接判定 ──
const isExternalDify = computed(
  () =>
    props.instance.engine_type === "DIFY" &&
    props.instance.dify_config?.external === true
);

const difyAppTypeLabel = computed(() => {
  const at = props.instance.dify_config?.app_type;
  if (!at) return "—";
  const m: Record<string, string> = {
    chat: t("agent.overview.dify.appType.chat"),
    agent: t("agent.overview.dify.appType.agent"),
    workflow: t("agent.overview.dify.appType.workflow")
  };
  return m[at] || at;
});

// ── 概览统计数据 ──
const stats = ref<InstanceOverview>({
  conversationCount: 0,
  totalTokens: 0,
  activeUsers: 0,
  conversationTrend: []
});

async function fetchOverview() {
  try {
    stats.value = await getInstanceOverviewApi(props.instance.id);
  } catch {
    // 静默失败，保持 0 值
  }
}

onMounted(fetchOverview);
watch(() => props.instance.id, fetchOverview);

// ── 部署状态 ──
const deployStatusConfig = computed<Record<string, { label: string; color: string }>>(() => ({
  RUNNING: { label: t("common.status.running"), color: "#00a870" },
  SUSPENDED: { label: t("common.status.suspended"), color: "#f59e0b" },
  FAILED: { label: t("common.status.failed"), color: "#f56c6c" },
  PENDING: { label: t("common.status.pending"), color: "#909399" },
  DEPLOYING: { label: t("common.status.deploying"), color: "#386bf5" },
  ARCHIVED: { label: t("common.status.archived"), color: "#909399" }
}));

// ── 统计卡片 ──
const statCards = computed(() => [
  {
    label: t("agent.overview.stat.conversation"),
    value: stats.value.conversationCount,
    icon: Chat1Line,
    color: "#41b6ff",
    bgColor: "#41b6ff18",
    percent: stats.value.conversationCount
      ? t("agent.overview.percent.real")
      : t("agent.overview.percent.noData")
  },
  {
    label: t("agent.overview.stat.token"),
    value: stats.value.totalTokens,
    icon: CoinLine,
    color: "#00a870",
    bgColor: "#00a87018",
    percent: stats.value.totalTokens
      ? t("agent.overview.percent.real")
      : t("agent.overview.percent.noData")
  },
  {
    label: t("agent.overview.stat.activeUser"),
    value: stats.value.activeUsers,
    icon: User3Line,
    color: "#f59e0b",
    bgColor: "#f59e0b18",
    percent: stats.value.activeUsers
      ? t("agent.overview.percent.real")
      : t("agent.overview.percent.noData")
  },
  {
    label: t("agent.overview.stat.deploy"),
    value:
      deployStatusConfig.value[props.deployStatus?.status || ""]?.label ||
      t("agent.overview.percent.notDeployed"),
    icon: ServerLine,
    color:
      deployStatusConfig.value[props.deployStatus?.status || ""]?.color || "#909399",
    bgColor:
      (deployStatusConfig.value[props.deployStatus?.status || ""]?.color || "#909399") +
      "18",
    percent: props.deployStatus?.engine_url
      ? t("agent.overview.percent.connected")
      : "—"
  }
]);

// ── 对话趋势图表 ──
const chartRef = ref<HTMLDivElement>();
const { setOptions: setChartOptions } = useECharts(chartRef, { theme });

watch(
  stats,
  async () => {
    await nextTick();
    const trend = stats.value.conversationTrend;
    setChartOptions({
      color: ["#41b6ff"],
      tooltip: {
        trigger: "axis",
        axisPointer: { type: "shadow" }
      },
      grid: {
        top: "20px",
        left: "40px",
        right: "10px",
        bottom: "30px"
      },
      xAxis: {
        type: "category",
        data: trend.map(p => dayjs(p.timestamp).format("MM-DD")),
        axisLabel: { fontSize: "0.75rem" },
        axisLine: { show: false },
        axisTick: { show: false }
      },
      yAxis: {
        type: "value",
        splitLine: { lineStyle: { type: "dashed", opacity: 0.3 } },
        axisLabel: { fontSize: "0.75rem" }
      },
      series: [
        {
          type: "line",
          data: trend.map(p => p.value),
          smooth: true,
          symbol: "circle",
          symbolSize: 6,
          lineStyle: { width: 2 },
          areaStyle: {
            color: {
              type: "linear",
              x: 0,
              y: 0,
              x2: 0,
              y2: 1,
              colorStops: [
                { offset: 0, color: "rgba(65, 182, 255, 0.3)" },
                { offset: 1, color: "rgba(65, 182, 255, 0.02)" }
              ]
            }
          }
        }
      ]
    } as any);
  },
  { immediate: true, deep: true }
);
</script>

<template>
  <div class="overview-tab">
    <!-- Dify 对接配置卡片（外部对接模式） -->
    <el-card v-if="isExternalDify" shadow="never" class="dify-config-card mb-3">
      <template #header>
        <span class="card-title">{{ t("agent.overview.dify.title") }}</span>
      </template>
      <el-descriptions :column="2" border>
        <el-descriptions-item :label="t('agent.overview.dify.baseUrl')">
          {{ props.instance.dify_config?.base_url || "—" }}
        </el-descriptions-item>
        <el-descriptions-item :label="t('agent.overview.dify.appTypeLabel')">
          {{ difyAppTypeLabel }}
        </el-descriptions-item>
        <el-descriptions-item :label="t('agent.overview.dify.appApiKey')">
          <code class="dify-key">{{ props.instance.dify_config?.app_api_key || "—" }}</code>
        </el-descriptions-item>
        <el-descriptions-item :label="t('agent.overview.dify.mode')">
          <el-tag size="small" type="warning">{{ t("agent.overview.dify.modeExternal") }}</el-tag>
        </el-descriptions-item>
      </el-descriptions>
    </el-card>

    <!-- 统计卡片行 + 趋势图（Hermes + Dify 外接共用，后端 build_instance_overview 透明返回同 schema） -->
    <el-row :gutter="12">
      <re-col
        v-for="(card, index) in statCards"
        :key="index"
        :value="6"
        :xs="24"
        :sm="12"
        :md="6"
        class="mb-2"
      >
        <el-card shadow="never" class="stat-card">
          <div class="stat-card-header">
            <span class="stat-card-label">{{ card.label }}</span>
            <div
              class="stat-card-icon"
              :style="{ backgroundColor: card.bgColor, color: card.color }"
            >
              <IconifyIconOffline
                :icon="card.icon"
                v-bind="{ width: '18', height: '18', color: card.color } as any"
              />
            </div>
          </div>
          <div class="stat-card-body">
            <template v-if="typeof card.value === 'number'">
              <ReNormalCountTo
                :endVal="card.value"
                :duration="1500"
                fontSize="1.8em"
                separator=","
              />
            </template>
            <template v-else>
              <span class="stat-card-text">{{ card.value }}</span>
            </template>
          </div>
          <p class="stat-card-percent" :style="{ color: card.color }">
            {{ card.percent }}
          </p>
        </el-card>
      </re-col>
    </el-row>

    <!-- 对话趋势图表 -->
    <el-row :gutter="12">
      <el-col :xs="24" class="mb-2">
        <el-card shadow="never">
          <template #header>
            <span class="card-title">{{ t("agent.overview.chart.trend") }}</span>
          </template>
          <div ref="chartRef" style="width: 100%; height: 300px" />
        </el-card>
      </el-col>
    </el-row>
  </div>
</template>

<style scoped>
.overview-tab {
  margin-bottom: 20px;
}

.stat-card {
  min-height: 110px;
  border-radius: 8px;
}

.stat-card-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin-bottom: 8px;
}

.stat-card-label {
  font-size: 13px;
  color: var(--el-text-color-secondary);
}

.stat-card-icon {
  display: flex;
  align-items: center;
  justify-content: center;
  width: 32px;
  height: 32px;
  border-radius: 8px;
}

.stat-card-body {
  display: flex;
  align-items: baseline;
  gap: 4px;
}

.stat-card-text {
  font-size: 1.8em;
  font-weight: 600;
  color: var(--el-text-color-primary);
}

.stat-card-percent {
  margin: 4px 0 0;
  font-size: 12px;
  font-weight: 500;
}

.card-title {
  font-size: 14px;
  font-weight: 600;
}

.dify-config-card {
  border-radius: 8px;
}

.dify-key {
  font-family: "SF Mono", Menlo, Monaco, Consolas, monospace;
  font-size: 13px;
  color: var(--el-text-color-primary);
  background: var(--el-fill-color-light);
  padding: 2px 6px;
  border-radius: 4px;
}
</style>
