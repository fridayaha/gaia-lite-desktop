<script setup lang="ts">
import DocsLink from "@/components/DocsLink/index.vue";
import { ref, onMounted, nextTick } from "vue";
import { useRoute } from "vue-router";
import { useDark, useECharts } from "@pureadmin/utils";
import { getQualityApi, type QualityByAgent } from "@/api/manager/observability";
import { getInstancesApi } from "@/api/manager/agentInstances";
import { getUserGroupsApi, type UserGroupResponse } from "@/api/manager/userGroups";

const { isDark } = useDark();
const chartTheme = isDark.value ? "dark" : "light";

const loading = ref(false);
const route = useRoute();
const langfuseConfigured = ref(true);
const langfuseUrl = ref("");
const overall = ref({
  request_count: 0,
  success_rate: 0,
  p50_latency_ms: 0,
  p95_latency_ms: 0,
  avg_tokens_per_request: 0
});
const byAgent = ref<QualityByAgent[]>([]);
const agentId = ref("");
const enduserId = ref("");
const groupId = ref("");
const days = ref<number | null>(7);
const timeRange = ref<[string, string] | null>(null);
const agentOptions = ref<{ id: string; name: string }[]>([]);
const filteredAgentOptions = ref<{ id: string; name: string }[]>([]);
const groupOptions = ref<UserGroupResponse[]>([]);

const latencyChartRef = ref<HTMLDivElement>();
const { setOptions: setLatencyOptions } = useECharts(latencyChartRef, { theme: chartTheme });

const shortcuts = [
  {
    text: "今天",
    value: () => {
      const end = new Date();
      const start = new Date();
      start.setHours(0, 0, 0, 0);
      return [start, end];
    }
  },
  {
    text: "近 7 天",
    value: () => {
      const end = new Date();
      const start = new Date();
      start.setTime(end.getTime() - 3600 * 1000 * 24 * 7);
      return [start, end];
    }
  },
  {
    text: "近 30 天",
    value: () => {
      const end = new Date();
      const start = new Date();
      start.setTime(end.getTime() - 3600 * 1000 * 24 * 30);
      return [start, end];
    }
  },
  {
    text: "本月",
    value: () => {
      const end = new Date();
      const start = new Date();
      start.setDate(1);
      start.setHours(0, 0, 0, 0);
      return [start, end];
    }
  }
];

function fmtLatency(ms: number): string {
  if (ms < 1000) return `${ms}ms`;
  return `${(ms / 1000).toFixed(2)}s`;
}

function fmtPercent(p: number): string {
  return `${(p * 100).toFixed(1)}%`;
}

function fmtAgentId(id: string): string {
  return id.length > 8 ? id.slice(0, 8) : id;
}

async function loadOptions() {
  try {
    const [agents, groups] = await Promise.all([
      getInstancesApi({ page: 1, page_size: 100 }),
      getUserGroupsApi()
    ]);
    agentOptions.value = (agents.items || []).map(i => ({ id: i.id, name: i.name }));
    filteredAgentOptions.value = agentOptions.value;
    groupOptions.value = groups || [];
  } catch {
    agentOptions.value = [];
    groupOptions.value = [];
  }
}

// 自定义过滤：支持按 name、完整 ID、ID 前 8 字符匹配
// el-select filterable 默认按 label 子串匹配，label 只含前 8 字符，
// 用户输入完整 UUID 反而匹配不到
function agentFilterMethod(q: string) {
  if (!q) {
    filteredAgentOptions.value = agentOptions.value;
    return;
  }
  const query = q.trim().toLowerCase();
  filteredAgentOptions.value = agentOptions.value.filter(
    a =>
      a.name.toLowerCase().includes(query) ||
      a.id.toLowerCase().includes(query) ||
      a.id.slice(0, 8).toLowerCase().includes(query)
  );
}

function onTimeRangeChange() {
  // 互斥：选了 datetimerange 后清空 days
  days.value = null;
  load();
}

function onDaysChange() {
  // 互斥：选了 days 后清空 timeRange
  timeRange.value = null;
  load();
}

async function load() {
  loading.value = true;
  try {
    const params: Record<string, any> = {};
    if (timeRange.value && timeRange.value.length === 2) {
      params.from_ts = timeRange.value[0];
      params.to_ts = timeRange.value[1];
    } else if (days.value) {
      // /quality 不接受 days 参数，前端转成 from_ts/to_ts
      const end = new Date();
      const start = new Date(end.getTime() - 3600 * 1000 * 24 * days.value);
      params.from_ts = start.toISOString();
      params.to_ts = end.toISOString();
    }
    if (agentId.value) params.agent_id = agentId.value;
    if (enduserId.value) params.enduser_id = enduserId.value;
    if (groupId.value) params.user_group_id = groupId.value;
    const resp = await getQualityApi(params);
    langfuseConfigured.value = resp.langfuse_configured;
    langfuseUrl.value = resp.langfuse_url || "";
    overall.value = resp.overall || overall.value;
    byAgent.value = resp.by_agent || [];
    await nextTick();
    renderLatency();
  } finally {
    loading.value = false;
  }
}

function resetFilters() {
  agentId.value = "";
  enduserId.value = "";
  groupId.value = "";
  days.value = 7;
  timeRange.value = null;
  filteredAgentOptions.value = agentOptions.value;
  load();
}

function renderLatency() {
  if (!latencyChartRef.value) return;
  const rows = byAgent.value.slice(0, 15);
  // x 轴显示"名称 (ID前8位)"，名称缺失时 fallback 到 ID 前 8 位
  const names = rows.map(i =>
    i.name ? `${i.name} (${i.agent_id.slice(0, 8)})` : fmtAgentId(i.agent_id)
  );
  const p50 = rows.map(i => Number(i.p50_latency_ms || 0));
  const p95 = rows.map(i => Number(i.p95_latency_ms || 0));
  setLatencyOptions({
    tooltip: { trigger: "axis" },
    legend: { data: ["P50", "P95"], top: 0 },
    grid: { left: 50, right: 20, top: 30, bottom: 60 },
    xAxis: { type: "category", data: names, axisLabel: { rotate: 35, interval: 0 } },
    yAxis: { type: "value", name: "ms" },
    series: [
      { name: "P50", type: "bar", data: p50, itemStyle: { color: "#386bf5", borderRadius: [4, 4, 0, 0] }, barMaxWidth: 24 },
      { name: "P95", type: "bar", data: p95, itemStyle: { color: "#f56c6c", borderRadius: [4, 4, 0, 0] }, barMaxWidth: 24 }
    ]
  } as any);
}

onMounted(() => {
  // 来自告警事件列表的跳转：预填 agent_id 过滤
  const aid = route.query.agent_id;
  if (typeof aid === "string" && aid) {
    agentId.value = aid;
  }
  loadOptions();
  load();
});
</script>

<template>
  <div class="main" v-loading="loading">
    <DocsLink to="monitoring.html#calls" />
    <div class="welcome">
      <div class="w-full flex flex-wrap items-center justify-between mb-4 gap-3">
        <div class="flex items-center gap-3 flex-wrap">
          <el-select
            v-model="agentId"
            filterable
            clearable
            placeholder="智能体或智能体ID"
            :filter-method="agentFilterMethod"
            style="width: 240px"
            @change="load"
            @visible-change="(v: boolean) => v && agentFilterMethod('')"
          >
            <el-option
              v-for="a in filteredAgentOptions"
              :key="a.id"
              :label="`${a.name} (${a.id.slice(0, 8)})`"
              :value="a.id"
            />
          </el-select>
          <el-input
            v-model="enduserId"
            clearable
            placeholder="终端用户 ID"
            style="width: 200px"
            @change="load"
          />
          <el-select
            v-model="groupId"
            filterable
            clearable
            placeholder="用户组"
            style="width: 180px"
            @change="load"
          >
            <el-option
              v-for="g in groupOptions"
              :key="g.id"
              :label="g.name"
              :value="g.id"
            />
          </el-select>
          <el-date-picker
            v-model="timeRange"
            type="datetimerange"
            range-separator="至"
            start-placeholder="开始时间"
            end-placeholder="结束时间"
            format="YYYY-MM-DD HH:mm:ss"
            value-format="YYYY-MM-DDTHH:mm:ssZ"
            :shortcuts="shortcuts"
            style="width: 380px"
            @change="onTimeRangeChange"
          />
          <el-select
            v-model="days"
            placeholder="快速选择"
            style="width: 120px"
            @change="onDaysChange"
          >
            <el-option label="近 7 天" :value="7" />
            <el-option label="近 30 天" :value="30" />
            <el-option label="近 90 天" :value="90" />
          </el-select>
        </div>
        <div class="flex items-center gap-3 flex-wrap">
          <el-button @click="resetFilters">重置</el-button>
          <el-button type="primary" @click="load">刷新</el-button>
        </div>
      </div>

      <el-alert
        v-if="!langfuseConfigured"
        title="Langfuse 未配置，调用分析数据不可用"
        type="info"
        :closable="false"
        class="mb-4"
      />

      <el-row :gutter="12" class="mb-4">
        <el-col :span="6">
          <el-card shadow="never" :body-style="{ padding: '12px 16px' }">
            <div class="text-xs text-gray-400">请求总数</div>
            <div class="text-2xl font-semibold mt-1">{{ overall.request_count }}</div>
          </el-card>
        </el-col>
        <el-col :span="6">
          <el-card shadow="never" :body-style="{ padding: '12px 16px' }">
            <div class="text-xs text-gray-400">成功率</div>
            <div class="text-2xl font-semibold mt-1" :class="overall.success_rate < 0.95 ? 'text-red-500' : ''">
              {{ fmtPercent(overall.success_rate) }}
            </div>
          </el-card>
        </el-col>
        <el-col :span="6">
          <el-card shadow="never" :body-style="{ padding: '12px 16px' }">
            <div class="text-xs text-gray-400">P50 延迟</div>
            <div class="text-2xl font-semibold mt-1">{{ fmtLatency(overall.p50_latency_ms) }}</div>
          </el-card>
        </el-col>
        <el-col :span="6">
          <el-card shadow="never" :body-style="{ padding: '12px 16px' }">
            <div class="text-xs text-gray-400">P95 延迟</div>
            <div class="text-2xl font-semibold mt-1" :class="overall.p95_latency_ms > 5000 ? 'text-red-500' : ''">
              {{ fmtLatency(overall.p95_latency_ms) }}
            </div>
          </el-card>
        </el-col>
      </el-row>

      <el-card shadow="never" class="mb-4">
        <template #header>
          <span class="card-title text-sm">各智能体延迟分布（P50 / P95）</span>
        </template>
        <div ref="latencyChartRef" style="height: 320px" />
      </el-card>

      <el-card shadow="never">
        <template #header>
          <span class="card-title text-sm">智能体调用明细</span>
        </template>
        <el-table :data="byAgent" stripe>
          <el-table-column label="智能体 ID" min-width="280">
            <template #default="{ row }"><span class="font-mono text-xs">{{ row.agent_id }}</span></template>
          </el-table-column>
          <el-table-column label="智能体" min-width="140">
            <template #default="{ row }">
              <span v-if="row.name">{{ row.name }}</span>
              <span v-else class="text-gray-400">—</span>
            </template>
          </el-table-column>
          <el-table-column label="请求数" prop="request_count" min-width="100" />
          <el-table-column label="成功率" min-width="100">
            <template #default="{ row }">{{ fmtPercent(row.success_rate) }}</template>
          </el-table-column>
          <el-table-column label="P50 延迟" min-width="120">
            <template #default="{ row }">{{ fmtLatency(row.p50_latency_ms) }}</template>
          </el-table-column>
          <el-table-column label="P95 延迟" min-width="120">
            <template #default="{ row }">{{ fmtLatency(row.p95_latency_ms) }}</template>
          </el-table-column>
          <el-table-column label="平均 Token" prop="avg_tokens" min-width="120" />
        </el-table>
      </el-card>
    </div>
  </div>
</template>

<style scoped>
.main { padding: 20px; }
.welcome { max-width: 1400px; margin: 0 auto; }
</style>
