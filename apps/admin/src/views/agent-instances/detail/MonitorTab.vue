<script setup lang="ts">
import { ref, computed, watch, nextTick } from "vue";
import { useI18n } from "vue-i18n";
import type { AgentInstanceResponse, InstanceMetrics } from "@/api/manager/agentInstances";
import { getInstanceMetricsApi } from "@/api/manager/agentInstances";
import { useDark, useECharts } from "@pureadmin/utils";
import Segmented from "@/components/ReSegmented";
import FeaturePlaceholder from "@/components/FeaturePlaceholder/index.vue";
import dayjs from "dayjs";

defineOptions({ name: "InstanceMonitorTab" });

const props = defineProps<{
  instanceId: string;
  instance?: AgentInstanceResponse | null;
}>();

const { t } = useI18n();
const { isDark } = useDark();
const theme = computed(() => (isDark.value ? "dark" : "light"));

const isExternalDify = computed(
  () =>
    props.instance?.engine_type === "DIFY" &&
    !!props.instance?.dify_config?.app_id &&
    !!props.instance?.dify_config?.base_url
);

const timeRangeIndex = ref(2); // 0: 1h, 1: 6h, 2: 24h, 3: 7d
const timeRangeLabels = ["1h", "6h", "24h", "7d"] as const;
const currentRange = computed(() => timeRangeLabels[timeRangeIndex.value]);

const loading = ref(false);
const metrics = ref<InstanceMetrics | null>(null);
const apiUnavailable = ref(false);

const timeRangeOptions = computed(() => [
  { label: t("agent.monitor.range.h1") },
  { label: t("agent.monitor.range.h6") },
  { label: t("agent.monitor.range.h24") },
  { label: t("agent.monitor.range.d7") }
]);

// ── 图表 refs ──
const cpuChartRef = ref<HTMLDivElement>();
const memoryChartRef = ref<HTMLDivElement>();
const requestsChartRef = ref<HTMLDivElement>();
const tokensChartRef = ref<HTMLDivElement>();

const { setOptions: setCpuOpts } = useECharts(cpuChartRef, { theme });
const { setOptions: setMemoryOpts } = useECharts(memoryChartRef, { theme });
const { setOptions: setRequestsOpts } = useECharts(requestsChartRef, { theme });
const { setOptions: setTokensOpts } = useECharts(tokensChartRef, { theme });

/** 生成通用折线图配置 */
function lineChartOptions(
  data: { timestamp: string; value: number }[],
  color: string,
  unit: string,
  smooth = true,
  markLineValue?: number
): any {
  const series: any = {
    type: "line",
    data: data.map(p => p.value),
    smooth,
    symbol: data.length <= 1 ? "circle" : "none",
    symbolSize: data.length <= 1 ? 8 : 0,
    lineStyle: { width: 2 },
    areaStyle: {
      color: {
        type: "linear",
        x: 0,
        y: 0,
        x2: 0,
        y2: 1,
        colorStops: [
          { offset: 0, color: `${color}33` },
          { offset: 1, color: `${color}05` }
        ]
      }
    }
  };
  if (markLineValue && markLineValue > 0) {
    series.markLine = {
      silent: true,
      symbol: "none",
      lineStyle: { type: "dashed", color: "#e85f33", width: 1.5 },
      label: {
        formatter: `${t("agent.monitor.chart.request")} ${markLineValue}${unit}`,
        fontSize: "0.7rem"
      },
      data: [{ yAxis: markLineValue }]
    };
  }
  return {
    color: [color],
    tooltip: {
      trigger: "axis",
      valueFormatter: (v: number) => `${v}${unit}`
    },
    grid: { top: "30px", left: "50px", right: "20px", bottom: "30px" },
    xAxis: {
      type: "category",
      data: data.map(p =>
        dayjs(p.timestamp).format(currentRange.value === "7d" ? "MM-DD" : "HH:mm")
      ),
      boundaryGap: false,
      axisLabel: { fontSize: "0.7rem", interval: "auto" as any },
      axisLine: { show: false },
      axisTick: { show: false },
      splitLine: { show: false }
    },
    yAxis: {
      type: "value",
      splitLine: { lineStyle: { type: "dashed", opacity: 0.3 } },
      axisLabel: { fontSize: "0.75rem", formatter: `{value}${unit}` }
    },
    series: [series]
  };
}

/** 渲染所有图表 */
function renderCharts() {
  if (!metrics.value) return;
  nextTick(() => {
    setCpuOpts(
      lineChartOptions(metrics.value.cpu, "#41b6ff", "m", true, metrics.value.resourceRequest?.cpu_m)
    );
    setMemoryOpts(
      lineChartOptions(
        metrics.value.memory,
        "#00a870",
        "Mi",
        true,
        metrics.value.resourceRequest?.memory_mi
      )
    );
    setRequestsOpts(lineChartOptions(metrics.value.requests, "#f59e0b", "", false));

    // Token 图表（输入/输出双系列）
    const tokenData = metrics.value.tokens;
    const tokenOpts: any = {
      color: ["#41b6ff", "#e85f33"],
      tooltip: { trigger: "axis" },
      legend: {
        data: [t("agent.monitor.chart.tokenIn"), t("agent.monitor.chart.tokenOut")],
        bottom: 0,
        textStyle: { fontSize: "0.75rem" }
      },
      grid: { top: "30px", left: "50px", right: "20px", bottom: "40px" },
      xAxis: {
        type: "category",
        data: tokenData.input.map((_, i) =>
          dayjs(tokenData.input[i].timestamp).format("HH:mm")
        ),
        boundaryGap: false,
        axisLabel: { fontSize: "0.7rem" },
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
          name: t("agent.monitor.chart.tokenIn"),
          type: "line",
          data: tokenData.input.map(p => p.value),
          smooth: true,
          symbol: "none",
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
        },
        {
          name: t("agent.monitor.chart.tokenOut"),
          type: "line",
          data: tokenData.output.map(p => p.value),
          smooth: true,
          symbol: "none",
          lineStyle: { width: 2 },
          areaStyle: {
            color: {
              type: "linear",
              x: 0,
              y: 0,
              x2: 0,
              y2: 1,
              colorStops: [
                { offset: 0, color: "rgba(232, 95, 51, 0.3)" },
                { offset: 1, color: "rgba(232, 95, 51, 0.02)" }
              ]
            }
          }
        }
      ]
    };
    setTokensOpts(tokenOpts);
  });
}

/** 从真实 API 加载指标 */
async function fetchMetrics(range: "1h" | "6h" | "24h" | "7d") {
  loading.value = true;
  apiUnavailable.value = false;
  try {
    metrics.value = await getInstanceMetricsApi(props.instanceId, { range });
  } catch (e: any) {
    if (e?.response?.status === 404 || e?.code === "ERR_NETWORK") {
      apiUnavailable.value = true;
    }
    metrics.value = null;
  } finally {
    loading.value = false;
  }
}

/** 切换时间范围 */
async function onRangeChange(val: number) {
  timeRangeIndex.value = val;
  const range = timeRangeLabels[val];
  await fetchMetrics(range);
}

watch(metrics, renderCharts);

// 初始化加载
fetchMetrics("24h");
</script>

<template>
  <div v-loading="loading" class="monitor-tab">
    <!-- 时间范围切换 -->
    <div class="monitor-toolbar mb-4">
      <Segmented
        v-model="timeRangeIndex"
        :options="timeRangeOptions"
        @change="({ index }: { index: number }) => onRangeChange(index)"
      />
    </div>

    <!-- 数据可用时：显示图表 -->
    <template v-if="!apiUnavailable && metrics">
      <el-row :gutter="16">
        <el-col v-if="!isExternalDify" :xs="24" :md="12" class="mb-4">
          <el-card shadow="never">
            <template #header>
              <span class="card-title">{{ t("agent.monitor.chart.cpu") }}</span>
            </template>
            <div ref="cpuChartRef" style="width: 100%; height: 260px" />
          </el-card>
        </el-col>
        <el-col v-if="!isExternalDify" :xs="24" :md="12" class="mb-4">
          <el-card shadow="never">
            <template #header>
              <span class="card-title">{{ t("agent.monitor.chart.memory") }}</span>
            </template>
            <div ref="memoryChartRef" style="width: 100%; height: 260px" />
          </el-card>
        </el-col>
        <el-col :xs="24" :md="12" class="mb-4">
          <el-card shadow="never">
            <template #header>
              <span class="card-title">{{ t("agent.monitor.chart.requests") }}</span>
            </template>
            <div ref="requestsChartRef" style="width: 100%; height: 260px" />
          </el-card>
        </el-col>
        <el-col :xs="24" :md="12" class="mb-4">
          <el-card shadow="never">
            <template #header>
              <span class="card-title">{{ t("agent.monitor.chart.token") }}</span>
            </template>
            <div ref="tokensChartRef" style="width: 100%; height: 260px" />
          </el-card>
        </el-col>
      </el-row>
    </template>

    <!-- API 不可用时：占位 -->
    <FeaturePlaceholder
      v-else-if="apiUnavailable"
      :title="t('agent.monitor.placeholder.title')"
      :description="t('agent.monitor.placeholder.desc')"
    />

    <!-- 首次加载中 -->
    <div v-else class="placeholder-state">
      <el-icon class="is-loading" :size="32"><svg /></el-icon>
      <p class="text-gray-400 mt-3">{{ t("agent.monitor.loading") }}</p>
    </div>
  </div>
</template>

<style scoped>
.monitor-tab {
  margin-bottom: 20px;
}

.monitor-toolbar {
  display: flex;
  justify-content: flex-start;
}

.card-title {
  font-size: 14px;
  font-weight: 600;
}

.placeholder-state {
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  padding: 60px 20px;
  text-align: center;
}
</style>
