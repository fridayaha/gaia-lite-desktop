<script setup lang="ts">
import DocsLink from "@/components/DocsLink/index.vue";
import { ref, onMounted, nextTick, watch } from "vue";
import { useDark, useECharts } from "@pureadmin/utils";
import {
  getServiceHealthApi,
  type ServiceHealthResponse,
  type ServiceHealthItem
} from "@/api/manager/observability";

const { isDark } = useDark();
const chartTheme = isDark.value ? "dark" : "light";

const loading = ref(false);
const range = ref<"1h" | "6h" | "24h" | "7d">("1h");
const customRange = ref<[Date, Date] | null>(null);
const metricsAvailable = ref(true);
const grafanaUrl = ref("");
const grafanaDashboardUid = ref("");
const overall = ref({ up_count: 0, total_count: 6, avg_p95_ms: null as number | null, avg_uptime_pct: 0 });
const items = ref<ServiceHealthItem[]>([]);
const trend = ref<{ ts: number; latencies: Record<string, number | null> }[]>([]);

const trendChartRef = ref<HTMLDivElement>();
const { setOptions: setTrendOptions } = useECharts(trendChartRef, { theme: chartTheme });

const SLO_LATENCY_MS = 500;

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
    const resp = await getServiceHealthApi(params);
    metricsAvailable.value = resp.metrics_available;
    grafanaUrl.value = resp.grafana_url || "";
    grafanaDashboardUid.value = resp.grafana_dashboard_uid || "";
    overall.value = resp.overall;
    items.value = resp.items || [];
    trend.value = resp.trend || [];
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
  const serviceNames = items.value.map(it => it.name);
  const series = serviceNames.map(name => ({
    name,
    type: "line",
    smooth: true,
    showSymbol: false,
    data: trend.value.map(t => t.latencies[name] ?? null),
    connectNulls: true
  }));
  setTrendOptions({
    tooltip: { trigger: "axis" },
    legend: { data: serviceNames, top: 0, type: "scroll" },
    grid: { left: 60, right: 30, top: 40, bottom: 40 },
    xAxis: { type: "category", data: times, axisLabel: { rotate: 0, interval: "auto" } },
    yAxis: { type: "value", name: "延迟 (ms)", min: 0 },
    series,
    markLine: {
      symbol: "none",
      data: [{ yAxis: SLO_LATENCY_MS, name: "SLO 500ms", lineStyle: { color: "#f56c6c", type: "dashed" } }]
    } as any
  } as any);
}

function openGrafana() {
  if (!grafanaUrl.value || !grafanaDashboardUid.value) return;
  window.open(`${grafanaUrl.value}/d/${grafanaDashboardUid.value}`, "_blank");
}

function resetFilters() {
  range.value = "1h";
  customRange.value = null;
  load();
}

function onPresetRangeChange() {
  customRange.value = null;
  load();
}

function onCustomRangeChange() {
  if (customRange.value) {
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
    <DocsLink to="monitoring.html#service-health" />
    <div class="welcome">
      <!-- 顶部筛选区 -->
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
          <el-button v-if="grafanaUrl" text size="small" @click="openGrafana">
            在 Grafana 中查看详细看板 →
          </el-button>
        </div>
      </div>

      <el-alert
        v-if="!metricsAvailable"
        type="info"
        :closable="false"
        title="Prometheus 未配置或不可达，服务健康数据不可用"
        class="mb-4"
      />

      <!-- 3 个顶部统计卡 -->
      <el-row :gutter="12" class="mb-4">
        <el-col :span="8">
          <el-card shadow="never" :body-style="{ padding: '12px 16px' }">
            <div class="text-xs text-gray-400">服务可用数</div>
            <div class="text-2xl font-semibold mt-1" :class="overall.up_count < overall.total_count ? 'text-red-500' : 'text-green-500'">
              {{ overall.up_count }} / {{ overall.total_count }}
            </div>
          </el-card>
        </el-col>
        <el-col :span="8">
          <el-card shadow="never" :body-style="{ padding: '12px 16px' }">
            <div class="text-xs text-gray-400">平均 p95 延迟</div>
            <div class="text-2xl font-semibold mt-1" :class="overall.avg_p95_ms && overall.avg_p95_ms > 500 ? 'text-red-500' : ''">
              {{ overall.avg_p95_ms !== null ? overall.avg_p95_ms + ' ms' : '—' }}
            </div>
          </el-card>
        </el-col>
        <el-col :span="8">
          <el-card shadow="never" :body-style="{ padding: '12px 16px' }">
            <div class="text-xs text-gray-400">平均可用率</div>
            <div class="text-2xl font-semibold mt-1" :class="overall.avg_uptime_pct < 99.5 ? 'text-red-500' : ''">
              {{ overall.avg_uptime_pct }}%
            </div>
          </el-card>
        </el-col>
      </el-row>

      <!-- 趋势图 -->
      <el-card shadow="never" class="mb-4">
        <template #header>
          <div class="flex items-center justify-between">
            <span class="card-title" style="font-size: 13px">服务延迟趋势 (ms)</span>
            <span class="text-xs text-gray-400">SLO 红色虚线 = 500ms</span>
          </div>
        </template>
        <div ref="trendChartRef" style="width: 100%; height: 320px" />
      </el-card>

      <!-- 服务列表表 -->
      <el-card shadow="never">
        <template #header>
          <span class="card-title" style="font-size: 13px">服务列表</span>
        </template>
        <el-table :data="items" stripe style="width: 100%">
          <el-table-column label="服务" prop="name" min-width="120" />
          <el-table-column label="状态" min-width="80">
            <template #default="{ row }">
              <span :class="row.status === 'ok' ? 'text-green-500' : 'text-red-500'">
                {{ row.status === 'ok' ? '● 正常' : '● 异常' }}
              </span>
            </template>
          </el-table-column>
          <el-table-column label="当前延迟" min-width="100">
            <template #default="{ row }">
              <span v-if="row.latency_ms !== null">{{ row.latency_ms }} ms{{ row.is_tcp ? ' (TCP)' : '' }}</span>
              <span v-else class="text-gray-300">—</span>
            </template>
          </el-table-column>
          <el-table-column label="p50 延迟" min-width="100">
            <template #default="{ row }">
              <span v-if="row.p50_ms !== null">{{ row.p50_ms }} ms</span>
              <span v-else class="text-gray-300">—</span>
            </template>
          </el-table-column>
          <el-table-column label="p95 延迟" min-width="100">
            <template #default="{ row }">
              <span v-if="row.p95_ms !== null" :class="row.p95_ms > 500 ? 'text-red-500' : ''">{{ row.p95_ms }} ms</span>
              <span v-else class="text-gray-300">—</span>
            </template>
          </el-table-column>
          <el-table-column label="可用率" min-width="90">
            <template #default="{ row }">
              <span :class="row.uptime_pct < 99.5 ? 'text-red-500' : ''">{{ row.uptime_pct }}%</span>
            </template>
          </el-table-column>
          <el-table-column label="SLO 达标" min-width="90">
            <template #default="{ row }">
              <span :class="row.slo_met ? 'text-green-500' : 'text-red-500'">
                {{ row.slo_met ? '✓ 达标' : '✗ 未达标' }}
              </span>
            </template>
          </el-table-column>
        </el-table>
        <el-empty v-if="!items.length" :image-size="50" />
      </el-card>
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
