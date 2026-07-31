<script setup lang="ts">
import DocsLink from "@/components/DocsLink/index.vue";
import { ref, computed, onMounted, nextTick } from "vue";
import { useRoute } from "vue-router";
import { useDark, useECharts } from "@pureadmin/utils";
import {
  getUsageApi,
  type UsageByAgent,
  type UsageByModel,
  type UsageByGroup,
  type UsageTrendPoint
} from "@/api/manager/observability";
import { getInstancesApi } from "@/api/manager/agentInstances";
import { getUserGroupsApi, type UserGroupResponse } from "@/api/manager/userGroups";

const { isDark } = useDark();
const chartTheme = isDark.value ? "dark" : "light";

const loading = ref(false);
const route = useRoute();
const days = ref<number | null>(30);
const timeRange = ref<[string, string] | null>(null);
const agentId = ref("");
const enduserId = ref("");
const groupId = ref("");

const todayTokens = ref(0);
const monthlyTokens = ref(0);
const monthlyCost = ref(0);
const byAgent = ref<UsageByAgent[]>([]);
const byModel = ref<UsageByModel[]>([]);
const byGroup = ref<UsageByGroup[]>([]);
const trend = ref<UsageTrendPoint[]>([]);
const litellmUrl = ref("");

const agentOptions = ref<{ id: string; name: string }[]>([]);
const groupOptions = ref<UserGroupResponse[]>([]);
const filteredAgentOptions = ref<{ id: string; name: string }[]>([]);

const agentChartRef = ref<HTMLDivElement>();
const modelChartRef = ref<HTMLDivElement>();
const trendChartRef = ref<HTMLDivElement>();
const groupChartRef = ref<HTMLDivElement>();
const { setOptions: setAgentOptions } = useECharts(agentChartRef, { theme: chartTheme });
const { setOptions: setModelOptions } = useECharts(modelChartRef, { theme: chartTheme });
const { setOptions: setTrendOptions } = useECharts(trendChartRef, { theme: chartTheme });
const { setOptions: setGroupOptions } = useECharts(groupChartRef, { theme: chartTheme });

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

function fmtNum(n: number): string {
  if (n >= 1_000_000) return `${(n / 1_000_000).toFixed(2)}M`;
  if (n >= 1000) return `${(n / 1000).toFixed(1)}K`;
  return String(n);
}

function fmtCost(c: number): string {
  return `¥${c.toFixed(2)}`;
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

function resetFilters() {
  agentId.value = "";
  enduserId.value = "";
  groupId.value = "";
  days.value = 30;
  timeRange.value = null;
  filteredAgentOptions.value = agentOptions.value;
  load();
}

async function load() {
  loading.value = true;
  try {
    const params: Record<string, any> = {};
    if (timeRange.value && timeRange.value.length === 2) {
      params.from_ts = timeRange.value[0];
      params.to_ts = timeRange.value[1];
    } else {
      params.days = days.value ?? 30;
    }
    if (agentId.value) params.agent_id = agentId.value;
    if (enduserId.value) params.enduser_id = enduserId.value;
    if (groupId.value) params.user_group_id = groupId.value;
    const resp = await getUsageApi(params);
    todayTokens.value = resp.today_tokens || 0;
    monthlyTokens.value = resp.monthly_tokens || 0;
    monthlyCost.value = resp.monthly_cost || 0;
    byAgent.value = resp.by_agent || [];
    byModel.value = resp.by_model || [];
    byGroup.value = resp.by_group || [];
    trend.value = resp.trend || [];
    litellmUrl.value = resp.litellm_url || "";
    await nextTick();
    renderAgent();
    renderModel();
    renderTrend();
    renderGroup();
  } finally {
    loading.value = false;
  }
}

// ── 组维度统计：top group + 活跃组数 ──
const topGroup = computed(() => {
  const sorted = [...byGroup.value].filter(g => Number(g.total_tokens || 0) > 0)
    .sort((a, b) => Number(b.total_tokens) - Number(a.total_tokens));
  return sorted.length ? sorted[0].name : "—";
});
const activeGroupCount = computed(() => byGroup.value.filter(g => Number(g.total_tokens || 0) > 0).length);

// ── 组明细占比（按 cost 占总和） ──
const groupCostSum = computed(() => byGroup.value.reduce((s, x) => s + Number(x.total_cost || 0), 0));
function groupRatio(val: number): string {
  if (!groupCostSum.value) return "—";
  return `${((Number(val || 0) / groupCostSum.value) * 100).toFixed(1)}%`;
}

function renderAgent() {
  if (!agentChartRef.value) return;
  const rows = byAgent.value.slice(0, 15);
  const names = rows.map(i => i.name || i.agent_id.slice(0, 8));
  const values = rows.map(i => Number(i.total_tokens || 0));
  setAgentOptions({
    tooltip: { trigger: "axis", formatter: (p: any) => `${p[0].name}: ${fmtNum(p[0].value)} tokens` },
    grid: { left: 50, right: 20, top: 20, bottom: 60 },
    xAxis: { type: "category", data: names, axisLabel: { rotate: 35, interval: 0 } },
    yAxis: { type: "value", name: "tokens", axisLabel: { formatter: (v: number) => fmtNum(v) } },
    series: [{ type: "bar", data: values, itemStyle: { color: "#386bf5", borderRadius: [4, 4, 0, 0] }, barMaxWidth: 36 }]
  } as any);
}

function renderModel() {
  if (!modelChartRef.value) return;
  const rows = byModel.value.slice(0, 15);
  const names = rows.map(i => i.model);
  const values = rows.map(i => Number(i.total_tokens || 0));
  setModelOptions({
    tooltip: { trigger: "axis", formatter: (p: any) => `${p[0].name}: ${fmtNum(p[0].value)} tokens` },
    grid: { left: 50, right: 20, top: 20, bottom: 60 },
    xAxis: { type: "category", data: names, axisLabel: { rotate: 35, interval: 0 } },
    yAxis: { type: "value", name: "tokens", axisLabel: { formatter: (v: number) => fmtNum(v) } },
    series: [{ type: "bar", data: values, itemStyle: { color: "#00a870", borderRadius: [4, 4, 0, 0] }, barMaxWidth: 36 }]
  } as any);
}

function renderTrend() {
  if (!trendChartRef.value) return;
  const dates = trend.value.map(i => i.date);
  const values = trend.value.map(i => Number(i.tokens || 0));
  setTrendOptions({
    tooltip: { trigger: "axis", formatter: (p: any) => `${p[0].name}: ${fmtNum(p[0].value)} tokens` },
    grid: { left: 50, right: 20, top: 20, bottom: 30 },
    xAxis: { type: "category", data: dates, boundaryGap: false },
    yAxis: { type: "value", name: "tokens", axisLabel: { formatter: (v: number) => fmtNum(v) } },
    series: [{
      type: "line",
      data: values,
      smooth: true,
      symbol: "circle",
      symbolSize: 6,
      lineStyle: { color: "#9b59b6", width: 2 },
      itemStyle: { color: "#9b59b6" },
      areaStyle: { color: "rgba(155,89,182,0.12)" }
    }]
  } as any);
}

function renderGroup() {
  if (!groupChartRef.value) return;
  const rows = byGroup.value.slice(0, 15);
  const names = rows.map(i => i.name || i.group_id.slice(0, 8));
  const values = rows.map(i => Number(i.total_tokens || 0));
  setGroupOptions({
    tooltip: { trigger: "axis", formatter: (p: any) => `${p[0].name}: ${fmtNum(p[0].value)} tokens` },
    grid: { left: 50, right: 20, top: 20, bottom: 60 },
    xAxis: { type: "category", data: names, axisLabel: { rotate: 35, interval: 0 } },
    yAxis: { type: "value", name: "tokens", axisLabel: { formatter: (v: number) => fmtNum(v) } },
    series: [{ type: "bar", data: values, itemStyle: { color: "#f59e0b", borderRadius: [4, 4, 0, 0] }, barMaxWidth: 36 }]
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
    <DocsLink to="monitoring.html#usage" />
    <div class="welcome">
      <div class="w-full flex flex-wrap items-center justify-between mb-4 gap-3">
        <div class="flex items-center gap-3 flex-wrap">
          <el-select
            v-model="agentId"
            filterable
            clearable
            placeholder="智能体"
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

      <el-row :gutter="12" class="mb-4">
        <el-col :span="6">
          <el-card shadow="never" :body-style="{ padding: '12px 16px' }">
            <div class="text-xs text-gray-400">今日 Token</div>
            <div class="text-2xl font-semibold mt-1">{{ fmtNum(todayTokens) }}</div>
          </el-card>
        </el-col>
        <el-col :span="6">
          <el-card shadow="never" :body-style="{ padding: '12px 16px' }">
            <div class="text-xs text-gray-400">本月 Token</div>
            <div class="text-2xl font-semibold mt-1">{{ fmtNum(monthlyTokens) }}</div>
          </el-card>
        </el-col>
        <el-col :span="6">
          <el-card shadow="never" :body-style="{ padding: '12px 16px' }">
            <div class="text-xs text-gray-400">本月成本</div>
            <div class="text-2xl font-semibold mt-1">{{ fmtCost(monthlyCost) }}</div>
          </el-card>
        </el-col>
        <el-col :span="6">
          <el-card shadow="never" :body-style="{ padding: '12px 16px' }">
            <div class="text-xs text-gray-400 flex items-center justify-between">
              <span>Top 组</span>
              <span class="text-[10px]">{{ activeGroupCount }} 组活跃</span>
            </div>
            <div class="text-2xl font-semibold mt-1 text-[#f59e0b]">{{ topGroup }}</div>
          </el-card>
        </el-col>
      </el-row>

      <el-row :gutter="12" class="mb-4">
        <el-col :span="24">
          <el-card shadow="never">
            <template #header>
              <span class="card-title text-sm">Token 趋势</span>
            </template>
            <div ref="trendChartRef" style="height: 280px" />
          </el-card>
        </el-col>
      </el-row>

      <el-row :gutter="12" class="mb-4">
        <el-col :span="12">
          <el-card shadow="never">
            <template #header>
              <span class="card-title text-sm">按智能体用量</span>
            </template>
            <div ref="agentChartRef" style="height: 280px" />
          </el-card>
        </el-col>
        <el-col :span="12">
          <el-card shadow="never">
            <template #header>
              <span class="card-title text-sm">按模型用量</span>
            </template>
            <div ref="modelChartRef" style="height: 280px" />
          </el-card>
        </el-col>
      </el-row>

      <el-row :gutter="12" class="mb-4">
        <el-col :span="24">
          <el-card shadow="never">
            <template #header>
              <span class="card-title text-sm">按组用量</span>
            </template>
            <div ref="groupChartRef" style="height: 280px" />
          </el-card>
        </el-col>
      </el-row>

      <el-card shadow="never" class="mb-4">
        <template #header>
          <span class="card-title text-sm">组明细</span>
        </template>
        <el-table :data="byGroup" stripe>
          <el-table-column label="组" prop="name" min-width="160" />
          <el-table-column label="Token 数" min-width="120">
            <template #default="{ row }">{{ fmtNum(row.total_tokens) }}</template>
          </el-table-column>
          <el-table-column label="成本" min-width="120">
            <template #default="{ row }">{{ fmtCost(row.total_cost) }}</template>
          </el-table-column>
          <el-table-column label="占比" width="120">
            <template #default="{ row }">{{ groupRatio(row.total_cost) }}</template>
          </el-table-column>
        </el-table>
      </el-card>

      <el-card shadow="never">
        <template #header>
          <span class="card-title text-sm">模型明细</span>
        </template>
        <el-table :data="byModel" stripe>
          <el-table-column label="模型" prop="model" min-width="200" />
          <el-table-column label="Token 数" min-width="120">
            <template #default="{ row }">{{ fmtNum(row.total_tokens) }}</template>
          </el-table-column>
          <el-table-column label="成本" min-width="120">
            <template #default="{ row }">{{ fmtCost(row.total_cost) }}</template>
          </el-table-column>
        </el-table>
      </el-card>
    </div>
  </div>
</template>

<style scoped>
.main { padding: 20px; }
.welcome { max-width: 1400px; margin: 0 auto; }
</style>
