<script setup lang="ts">
import DocsLink from "@/components/DocsLink/index.vue";
import { ref, reactive, computed, onMounted } from "vue";
import { ElMessage } from "element-plus";
import { searchLogsApi, type LogSearchItem } from "@/api/manager/observability";

const loading = ref(false);
const items = ref<LogSearchItem[]>([]);
const total = ref(0);
const grafanaUrl = ref("");
const currentQuery = ref("");

const filters = reactive({
  service: "",
  level: "",
  request_id: "",
  keyword: "",
  timeRange: null as [string, string] | null,
  minutes: 60 as number | null
});

const limit = ref(100);

const serviceOptions = [
  { label: "Manager", value: "manager" },
  { label: "Gateway", value: "gateway" }
];

const levelOptions = [
  { label: "INFO", value: "INFO" },
  { label: "WARNING", value: "WARNING" },
  { label: "ERROR", value: "ERROR" }
];

const shortcuts = [
  { text: "今天", value: () => { const e = new Date(); const s = new Date(); s.setHours(0,0,0,0); return [s, e]; } },
  { text: "近 1 小时", value: () => { const e = new Date(); const s = new Date(); s.setTime(e.getTime() - 3600*1000); return [s, e]; } },
  { text: "近 6 小时", value: () => { const e = new Date(); const s = new Date(); s.setTime(e.getTime() - 6*3600*1000); return [s, e]; } },
  { text: "近 24 小时", value: () => { const e = new Date(); const s = new Date(); s.setTime(e.getTime() - 24*3600*1000); return [s, e]; } }
];

const grafanaConfigured = computed(() => Boolean(grafanaUrl.value));

const grafanaExploreUrl = computed(() => {
  if (!grafanaUrl.value) return "";
  // 构造 Grafana Explore URL：left 参数是 JSON，包含 datasource + query + range
  const expr = currentQuery.value || buildLogql();
  const from = filters.timeRange && filters.timeRange.length === 2
    ? filters.timeRange[0]
    : (filters.minutes ? `now-${filters.minutes}m` : "now-1h");
  const leftObj = {
    datasource: "Loki",
    queries: [{ refId: "A", expr, queryType: "instant" }],
    range: { from, to: "now" }
  };
  return `${grafanaUrl.value}/explore?orgId=1&left=${encodeURIComponent(JSON.stringify(leftObj))}`;
});

function buildLogql(): string {
  const selectors = ['namespace="unionagents"'];
  if (filters.service) {
    selectors.push(`service="${filters.service}"`);
  } else {
    selectors.push('service=~"manager|gateway"');
  }
  if (filters.level) selectors.push(`level="${filters.level}"`);
  let expr = "{" + selectors.join(",") + "}";
  expr += ' != "uvicorn.access"';
  if (filters.keyword) expr += ` |= "${filters.keyword}"`;
  if (filters.request_id) expr += ` |= "${filters.request_id}"`;
  expr += ' | json | path != "/health" | path != "/metrics"';
  return expr;
}

async function load() {
  loading.value = true;
  try {
    const params: Record<string, any> = { limit: limit.value };
    if (filters.service) params.service = filters.service;
    if (filters.level) params.level = filters.level;
    if (filters.request_id) params.request_id = filters.request_id;
    if (filters.keyword) params.keyword = filters.keyword;
    if (filters.timeRange && filters.timeRange.length === 2) {
      params.time_from = filters.timeRange[0];
      params.time_to = filters.timeRange[1];
    }
    const resp = await searchLogsApi(params);
    items.value = resp.items;
    total.value = resp.total;
    grafanaUrl.value = resp.grafana_url || "";
    currentQuery.value = resp.query;
  } catch (e: any) {
    const detail = e?.response?.data?.detail || e?.message || "查询失败";
    ElMessage.error(detail);
  } finally {
    loading.value = false;
  }
}

function resetFilters() {
  filters.service = "";
  filters.level = "";
  filters.request_id = "";
  filters.keyword = "";
  filters.timeRange = null;
  filters.minutes = 60;
  load();
}

function onSearch() {
  load();
}

function levelTagType(level: string): "primary" | "success" | "warning" | "info" | "danger" {
  if (level === "ERROR") return "danger";
  if (level === "WARNING" || level === "WARN") return "warning";
  return "info";
}

function formatTime(ts: string): string {
  if (!ts) return "";
  try {
    return new Date(ts).toLocaleString("zh-CN", { hour12: false });
  } catch {
    return ts;
  }
}

function formatRaw(item: LogSearchItem): string {
  try {
    return JSON.stringify(item.raw, null, 2);
  } catch {
    return String(item.raw);
  }
}

onMounted(() => load());
</script>

<template>
  <div class="main">
    <DocsLink to="monitoring.html#log-search" />
    <div class="w-full flex flex-wrap items-center justify-between mb-4 gap-3">
      <div class="flex items-center gap-3 flex-wrap">
        <el-select
          v-model="filters.service"
          clearable
          placeholder="服务"
          style="width: 140px"
          @change="onSearch"
        >
          <el-option v-for="s in serviceOptions" :key="s.value" :label="s.label" :value="s.value" />
        </el-select>
        <el-select
          v-model="filters.level"
          clearable
          placeholder="级别"
          style="width: 120px"
          @change="onSearch"
        >
          <el-option v-for="l in levelOptions" :key="l.value" :label="l.label" :value="l.value" />
        </el-select>
        <el-input
          v-model="filters.request_id"
          placeholder="Request ID"
          clearable
          style="width: 200px"
          @keyup.enter="onSearch"
          @clear="onSearch"
        />
        <el-input
          v-model="filters.keyword"
          placeholder="日志关键字"
          clearable
          style="width: 200px"
          @keyup.enter="onSearch"
          @clear="onSearch"
        />
        <el-date-picker
          v-model="filters.timeRange"
          type="datetimerange"
          range-separator="至"
          start-placeholder="开始时间"
          end-placeholder="结束时间"
          format="YYYY-MM-DD HH:mm:ss"
          value-format="YYYY-MM-DDTHH:mm:ssZ"
          :shortcuts="shortcuts"
          style="width: 380px"
          @change="onSearch"
        />
      </div>
      <div class="flex items-center gap-3 flex-wrap">
        <el-button @click="resetFilters">重置</el-button>
        <el-button type="primary" @click="onSearch">刷新</el-button>
        <a
          v-if="grafanaConfigured"
          :href="grafanaExploreUrl"
          target="_blank"
          class="text-xs text-blue-500 hover:underline"
        >在 Grafana 中查看完整版 →</a>
      </div>
    </div>

    <el-table
      v-loading="loading"
      :data="items"
      stripe
      style="width: 100%"
      :default-sort="{ prop: 'loki_ts', order: 'descending' }"
    >
      <el-table-column type="expand">
        <template #default="{ row }">
          <pre class="m-2 p-3 bg-gray-50 rounded text-xs overflow-auto">{{ formatRaw(row) }}</pre>
        </template>
      </el-table-column>
      <el-table-column label="时间" width="200" prop="loki_ts" sortable>
        <template #default="{ row }">{{ formatTime(row.ts) }}</template>
      </el-table-column>
      <el-table-column prop="service" label="服务" width="100" />
      <el-table-column label="级别" width="100">
        <template #default="{ row }">
          <el-tag :type="levelTagType(row.level)" size="small">{{ row.level }}</el-tag>
        </template>
      </el-table-column>
      <el-table-column prop="logger" label="Logger" width="180" show-overflow-tooltip />
      <el-table-column prop="message" label="消息" min-width="400" show-overflow-tooltip />
      <el-table-column label="Request ID" width="140" show-overflow-tooltip>
        <template #default="{ row }">
          <span v-if="row.request_id" class="text-xs font-mono text-gray-500">{{ row.request_id }}</span>
          <span v-else>—</span>
        </template>
      </el-table-column>
    </el-table>

    <div class="mt-4 flex items-center justify-between">
      <div class="text-xs text-gray-500">
        共 {{ total }} 条 · LogQL: <code class="text-gray-700">{{ currentQuery }}</code>
      </div>
      <el-pagination
        v-model:current-page="limit"
        :total="total"
        :page-size="limit"
        :page-sizes="[100, 200, 500]"
        layout="sizes, total"
        @size-change="(v: number) => { limit = v; load(); }"
      />
    </div>
  </div>
</template>
