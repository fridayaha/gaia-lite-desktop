<script setup lang="ts">
import { ref, computed, onMounted, nextTick } from "vue";
import { useI18n } from "vue-i18n";
import { useDark, useECharts } from "@pureadmin/utils";
import { getPoolMetricsApi } from "@/api/manager/resourcePools";
import { ElMessage } from "element-plus";
import Segmented from "@/components/ReSegmented";
import dayjs from "dayjs";

defineOptions({ name: "ResourcePoolMonitorTab" });

const props = defineProps<{ poolId: string }>();
const { t } = useI18n();
const { isDark } = useDark();
const theme = computed(() => (isDark.value ? "dark" : "light"));

const loading = ref(false);
const timeRangeIndex = ref(2); // 0: 1h, 1: 6h, 2: 24h, 3: 7d
const timeRangeLabels = ["1h", "6h", "24h", "7d"] as const;
const currentRange = computed(() => timeRangeLabels[timeRangeIndex.value]);

const timeRangeOptions = computed(() => [
  { label: t("agent.monitor.range.h1") },
  { label: t("agent.monitor.range.h6") },
  { label: t("agent.monitor.range.h24") },
  { label: t("agent.monitor.range.d7") }
]);

const cpuChartRef = ref<HTMLDivElement>();
const memChartRef = ref<HTMLDivElement>();
const { setOptions: setCpuOpts } = useECharts(cpuChartRef, { theme });
const { setOptions: setMemOpts } = useECharts(memChartRef, { theme });

function chartOptions(
  data: { timestamp: string; value: number }[],
  color: string,
  unit: string,
  markLineValue?: number
): any {
  const series: any = {
    type: "line",
    data: data.map(p => p.value),
    smooth: true,
    symbol: data.length <= 1 ? "circle" : "none",
    symbolSize: data.length <= 1 ? 8 : 0,
    lineStyle: { width: 2, color },
    areaStyle: {
      color: {
        type: "linear",
        x: 0, y: 0, x2: 0, y2: 1,
        colorStops: [
          { offset: 0, color: color + "33" },
          { offset: 1, color: color + "05" }
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
    tooltip: { trigger: "axis", valueFormatter: (v: number) => `${v}${unit}` },
    grid: { top: 20, left: 50, right: 20, bottom: 30 },
    xAxis: {
      type: "category",
      data: data.map(p =>
        dayjs(p.timestamp).format(currentRange.value === "7d" ? "MM-DD" : "HH:mm")
      ),
      boundaryGap: false,
      axisLabel: { fontSize: "0.7rem" },
      axisLine: { show: false }
    },
    yAxis: {
      type: "value",
      splitLine: { lineStyle: { type: "dashed", opacity: 0.2 } },
      axisLabel: { fontSize: "0.75rem", formatter: `{value}${unit}` }
    },
    series: [series]
  };
}

async function fetchMetrics(range: "1h" | "6h" | "24h" | "7d") {
  loading.value = true;
  try {
    const data = await getPoolMetricsApi(props.poolId, { range });
    await nextTick();
    setCpuOpts(chartOptions(data.cpu, "#41b6ff", "m", data.resourceRequest?.cpu_m));
    setMemOpts(chartOptions(data.memory, "#00a870", "Mi", data.resourceRequest?.memory_mi));
  } catch {
    ElMessage.warning(t("engine.monitor.loadFailed"));
  } finally {
    loading.value = false;
  }
}

async function onRangeChange(val: number) {
  timeRangeIndex.value = val;
  await fetchMetrics(timeRangeLabels[val]);
}

onMounted(() => fetchMetrics("24h"));
</script>

<template>
  <div v-loading="loading" class="monitor-tab">
    <div class="monitor-toolbar mb-4">
      <Segmented
        v-model="timeRangeIndex"
        :options="timeRangeOptions"
        @change="({ index }: { index: number }) => onRangeChange(index)"
      />
    </div>
    <el-row :gutter="16">
      <el-col :span="12" class="mb-4">
        <el-card shadow="never">
          <template #header>
            <span class="text-sm font-semibold">{{ t("engine.monitor.chart.cpu") }}</span>
          </template>
          <div ref="cpuChartRef" style="width: 100%; height: 260px" />
        </el-card>
      </el-col>
      <el-col :span="12" class="mb-4">
        <el-card shadow="never">
          <template #header>
            <span class="text-sm font-semibold">{{ t("engine.monitor.chart.memory") }}</span>
          </template>
          <div ref="memChartRef" style="width: 100%; height: 260px" />
        </el-card>
      </el-col>
    </el-row>
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
</style>
