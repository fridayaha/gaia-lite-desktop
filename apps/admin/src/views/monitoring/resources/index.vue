<script setup lang="ts">
import DocsLink from "@/components/DocsLink/index.vue";
import { ref, onMounted, nextTick, watch } from "vue";
import { useDark, useECharts } from "@pureadmin/utils";
import { getResourcesApi, type ResourcesResponse, type ResourceTopNode, type ResourceTopPod } from "@/api/manager/observability";

const { isDark } = useDark();
const chartTheme = isDark.value ? "dark" : "light";

const loading = ref(false);
const range = ref<"1h" | "6h" | "24h" | "7d">("1h");
const customRange = ref<[Date, Date] | null>(null);
const metricsAvailable = ref(true);
const grafanaUrl = ref("");
const cluster = ref({ cpu_pct: 0, memory_pct: 0, pod_count: 0 });
const trend = ref<{ ts: number; cpu_pct: number; memory_pct: number }[]>([]);
const topNodes = ref<ResourceTopNode[]>([]);
const topPods = ref<ResourceTopPod[]>([]);
const firingAlerts = ref(0);

const trendChartRef = ref<HTMLDivElement>();
const { setOptions: setTrendOptions } = useECharts(trendChartRef, { theme: chartTheme });

async function load() {
  loading.value = true;
  try {
    const params: { range?: "1h" | "6h" | "24h" | "7d"; start_ts?: number; end_ts?: number } = {};
    if (customRange.value) {
      params.start_ts = Math.floor(customRange.value[0].getTime() / 1000);
      params.end_ts = Math.floor(customRange.value[1].getTime() / 1000);
    } else {
      params.range = range.value;
    }
    const resp = await getResourcesApi(params);
    metricsAvailable.value = resp.metrics_available;
    grafanaUrl.value = resp.grafana_url || "";
    cluster.value = resp.cluster;
    trend.value = resp.trend || [];
    topNodes.value = resp.top_nodes || [];
    topPods.value = resp.top_pods || [];
    firingAlerts.value = resp.firing_alerts || 0;
    await nextTick();
    renderTrend();
  } catch {
    // 静默失败
  } finally {
    loading.value = false;
  }
}

function renderTrend() {
  if (!trendChartRef.value) return;
  const times = trend.value.map(t => new Date(t.ts * 1000).toLocaleTimeString("zh-CN", { hour12: false }));
  const cpu = trend.value.map(t => t.cpu_pct);
  const mem = trend.value.map(t => t.memory_pct);
  setTrendOptions({
    tooltip: { trigger: "axis" },
    legend: { data: ["CPU 使用率", "内存使用率"], top: 0 },
    grid: { left: 50, right: 50, top: 30, bottom: 40 },
    xAxis: { type: "category", data: times, axisLabel: { rotate: 0, interval: "auto" } },
    yAxis: [
      { type: "value", name: "CPU %", min: 0, max: 100 },
      { type: "value", name: "内存 %", min: 0, max: 100 }
    ],
    series: [
      {
        name: "CPU 使用率",
        type: "line",
        smooth: true,
        showSymbol: false,
        data: cpu,
        itemStyle: { color: "#41b6ff" },
        areaStyle: { opacity: 0.1 }
      },
      {
        name: "内存使用率",
        type: "line",
        smooth: true,
        showSymbol: false,
        yAxisIndex: 1,
        data: mem,
        itemStyle: { color: "#00a870" },
        areaStyle: { opacity: 0.1 }
      }
    ]
  } as any);
}

function openGrafana(uid: string) {
  if (!grafanaUrl.value) return;
  window.open(`${grafanaUrl.value}/d/${uid}`, "_blank");
}

function resetFilters() {
  range.value = "1h";
  customRange.value = null;
  load();
}

function onPresetRangeChange() {
  // 选预设时清掉自定义范围
  customRange.value = null;
  load();
}

function onCustomRangeChange() {
  if (customRange.value) {
    // 选了自定义范围，预设保留显示但请求以 customRange 为准
    load();
  }
}

watch(range, () => onPresetRangeChange());

onMounted(() => {
  load();
});
</script>

<template>
  <div class="main" v-loading="loading">
    <DocsLink to="monitoring.html#resources" />
    <div class="welcome">
      <!-- 顶部筛选区：按钮在左，筛选+外链在右 -->
      <div class="w-full flex flex-wrap items-center justify-between mb-4 gap-3">
        <div class="flex items-center gap-3 flex-wrap">
          <el-select v-model="range" style="width: 140px" :disabled="!!customRange">
            <el-option label="近 1 小时" value="1h" />
            <el-option label="近 6 小时" value="6h" />
            <el-option label="近 24 小时" value="24h" />
            <el-option label="近 7 天" value="7d" />
          </el-select>
          <span class="text-xs text-gray-400">或自定义：</span>
          <el-date-picker
            v-model="customRange"
            type="datetimerange"
            range-separator="至"
            start-placeholder="开始时间"
            end-placeholder="结束时间"
            format="YYYY-MM-DD HH:mm"
            :clearable="true"
            style="width: 360px"
            @change="onCustomRangeChange"
          />
        </div>
        <div class="flex items-center gap-3 flex-wrap">
          <el-button @click="resetFilters">重置</el-button>
          <el-button type="primary" @click="load">刷新</el-button>
          <a
            v-if="grafanaUrl"
            :href="grafanaUrl"
            target="_blank"
            class="text-xs text-blue-500 hover:underline"
          >在 Grafana 中查看详细看板 →</a>
        </div>
      </div>

      <el-alert
        v-if="!metricsAvailable"
        type="info"
        :closable="false"
        title="Prometheus 未配置或不可达，资源监控数据不可用"
        class="mb-4"
      />

      <!-- 3 个顶部统计卡 -->
      <el-row :gutter="12" class="mb-4">
        <el-col :span="8">
          <el-card shadow="never" :body-style="{ padding: '12px 16px' }">
            <div class="text-xs text-gray-400">集群 CPU 使用率</div>
            <div class="text-2xl font-semibold mt-1" :class="cluster.cpu_pct > 80 ? 'text-red-500' : cluster.cpu_pct > 60 ? 'text-yellow-500' : ''">
              {{ cluster.cpu_pct }}%
            </div>
          </el-card>
        </el-col>
        <el-col :span="8">
          <el-card shadow="never" :body-style="{ padding: '12px 16px' }">
            <div class="text-xs text-gray-400">集群内存使用率</div>
            <div class="text-2xl font-semibold mt-1" :class="cluster.memory_pct > 80 ? 'text-red-500' : cluster.memory_pct > 60 ? 'text-yellow-500' : ''">
              {{ cluster.memory_pct }}%
            </div>
          </el-card>
        </el-col>
        <el-col :span="8">
          <el-card shadow="never" :body-style="{ padding: '12px 16px' }">
            <div class="text-xs text-gray-400">运行 Pod 数（unionagents）</div>
            <div class="text-2xl font-semibold mt-1">{{ cluster.pod_count }}</div>
          </el-card>
        </el-col>
      </el-row>

      <!-- 趋势图 -->
      <el-card shadow="never" class="mb-4">
        <template #header>
          <div class="flex items-center justify-between">
            <span class="card-title" style="font-size: 13px">CPU / 内存使用率趋势</span>
            <el-tag v-if="firingAlerts > 0" size="small" type="danger" effect="plain">
              当前告警 {{ firingAlerts }} 条
            </el-tag>
          </div>
        </template>
        <div ref="trendChartRef" style="width: 100%; height: 280px" />
      </el-card>

      <!-- Top 节点 + Top Pod 两列 -->
      <el-row :gutter="12">
        <el-col :xs="24" :sm="12">
          <el-card shadow="never">
            <template #header>
              <div class="flex items-center justify-between">
                <span class="card-title" style="font-size: 13px">Top 5 节点（按 CPU）</span>
                <el-button text size="small" @click="openGrafana('node-resources')">Grafana</el-button>
              </div>
            </template>
            <el-table :data="topNodes" stripe style="width: 100%">
              <el-table-column label="节点" prop="instance" min-width="160" />
              <el-table-column label="CPU%" min-width="80">
                <template #default="{ row }">
                  <span :class="row.cpu_pct > 80 ? 'text-red-500' : ''">{{ row.cpu_pct }}</span>
                </template>
              </el-table-column>
              <el-table-column label="内存%" prop="memory_pct" min-width="80" />
              <el-table-column label="磁盘%" prop="disk_pct" min-width="80" />
            </el-table>
            <el-empty v-if="!topNodes.length" :image-size="50" />
          </el-card>
        </el-col>
        <el-col :xs="24" :sm="12">
          <el-card shadow="never">
            <template #header>
              <div class="flex items-center justify-between">
                <span class="card-title" style="font-size: 13px">Top 5 Pod（按 CPU，unionagents ns）</span>
                <el-button text size="small" @click="openGrafana('engine-pods')">Grafana</el-button>
              </div>
            </template>
            <el-table :data="topPods" stripe style="width: 100%">
              <el-table-column label="Pod" prop="pod" min-width="200" />
              <el-table-column label="CPU（核）" prop="cpu_used_cores" min-width="90" />
              <el-table-column label="内存（MB）" prop="memory_used_mb" min-width="100" />
              <el-table-column label="重启" prop="restarts" min-width="70">
                <template #default="{ row }">
                  <span :class="row.restarts > 0 ? 'text-yellow-500' : ''">{{ row.restarts }}</span>
                </template>
              </el-table-column>
            </el-table>
            <el-empty v-if="!topPods.length" :image-size="50" />
          </el-card>
        </el-col>
      </el-row>
    </div>
  </div>
</template>

<style scoped>
.main {
  padding: 16px;
}
.welcome {
  max-width: 1400px;
  margin: 0 auto;
}
.card-title {
  font-weight: 500;
}
</style>
