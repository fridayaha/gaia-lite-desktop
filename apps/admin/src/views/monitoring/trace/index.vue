<script setup lang="ts">
import DocsLink from "@/components/DocsLink/index.vue";
import { ref, onMounted, computed, watch } from "vue";
import { useRoute } from "vue-router";
import { ElMessage } from "element-plus";
import { copyTextToClipboard } from "@pureadmin/utils";
import FileCopyLine from "~icons/ri/file-copy-line";
import { getTracesApi, getTraceDetailApi, getHermesCorrelationApi, type TraceItem, type TraceDetail, type ObservationItem } from "@/api/manager/observability";
import { getInstancesApi } from "@/api/manager/agentInstances";

const loading = ref(false);
const items = ref<TraceItem[]>([]);
const route = useRoute();

function copyTraceId(id: string) {
  if (copyTextToClipboard(id)) {
    ElMessage.success("Trace ID 已复制");
  } else {
    ElMessage.error("复制失败，请手动选择复制");
  }
}
const total = ref(0);
const langfuseConfigured = ref(true);
const langfuseUrl = ref("");
const agentId = ref("");
const enduserId = ref("");
const channelType = ref("");
const sessionFilter = ref("");
const agentOptions = ref<{ id: string; name: string }[]>([]);
const filteredAgentOptions = ref<{ id: string; name: string }[]>([]);
const limit = ref(50);
const offset = ref(0);
const timeRange = ref<[string, string] | null>(null);
const minutes = ref<number | null>(30);

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

const errorCount = computed(() => items.value.filter(i => i.status === "error").length);
const avgLatency = computed(() => {
  const latencies = items.value.map(i => i.latency_ms).filter((v): v is number => v != null);
  if (!latencies.length) return 0;
  return Math.round(latencies.reduce((a, b) => a + b, 0) / latencies.length);
});

async function loadAgents() {
  try {
    const resp = await getInstancesApi({ page: 1, page_size: 100 });
    agentOptions.value = (resp.items || []).map(i => ({ id: i.id, name: i.name }));
    filteredAgentOptions.value = agentOptions.value;
  } catch {
    agentOptions.value = [];
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
  // 互斥：选了 datetimerange 后清空 minutes
  minutes.value = null;
  offset.value = 0;
  load();
}

function onMinutesChange() {
  // 互斥：选了 minutes 后清空 timeRange
  timeRange.value = null;
  offset.value = 0;
  load();
}

async function load() {
  loading.value = true;
  try {
    const params: { agent_id?: string; enduser_id?: string; channel_type?: string; session_id?: string; from_ts?: string; to_ts?: string; limit: number; offset: number } = {
      limit: limit.value,
      offset: offset.value
    };
    if (agentId.value) params.agent_id = agentId.value;
    if (enduserId.value) params.enduser_id = enduserId.value;
    if (channelType.value) params.channel_type = channelType.value;
    if (sessionFilter.value) params.session_id = sessionFilter.value;
    if (timeRange.value && timeRange.value.length === 2) {
      params.from_ts = timeRange.value[0];
      params.to_ts = timeRange.value[1];
    } else if (minutes.value) {
      // /traces 不接受 minutes 参数，前端转成 from_ts/to_ts
      const end = new Date();
      const start = new Date(end.getTime() - 60 * 1000 * minutes.value);
      params.from_ts = start.toISOString();
      params.to_ts = end.toISOString();
    }
    const resp = await getTracesApi(params);
    items.value = resp.items || [];
    total.value = resp.total || 0;
    langfuseConfigured.value = resp.langfuse_configured;
    langfuseUrl.value = resp.langfuse_url || "";
  } catch {
    items.value = [];
  } finally {
    loading.value = false;
  }
}

function openInLangfuse(traceId: string) {
  if (!langfuseUrl.value) return;
  // Langfuse v3 单条 trace URL 为 /trace/{id}（服务端 307 重定向到 /project/{projectId}/traces/{id}）。
  // 直接拼 /traces/{id} 会 404。
  window.open(`${langfuseUrl.value}/trace/${traceId}`, "_blank");
}

function formatTime(ts: string | null): string {
  if (!ts) return "—";
  try {
    const d = new Date(ts);
    // 手动拼毫秒，避免某些环境 Intl ICU 数据精简时 fractionalSecondDigits 让 toLocaleString
    // 退化成只返回毫秒数字（Node small-icu 即如此）
    const base = d.toLocaleString("zh-CN", { hour12: false });
    const ms = String(d.getMilliseconds()).padStart(3, "0");
    return `${base}.${ms}`;
  } catch {
    return ts;
  }
}

function formatLatency(ms: number | null): string {
  if (ms == null) return "—";
  if (ms < 1000) return `${ms}ms`;
  return `${(ms / 1000).toFixed(2)}s`;
}

function formatAgentId(id: string | null): string {
  if (!id) return "—";
  return id.length > 8 ? id.slice(0, 8) : id;
}

function agentDisplayName(id: string | null): string {
  if (!id) return "—";
  const found = agentOptions.value.find(o => o.id === id);
  return found ? found.name : id.slice(0, 8);
}

function handlePageChange(p: number) {
  offset.value = (p - 1) * limit.value;
  load();
}

function resetFilters() {
  agentId.value = "";
  enduserId.value = "";
  channelType.value = "";
  sessionFilter.value = "";
  minutes.value = 30;
  timeRange.value = null;
  filteredAgentOptions.value = agentOptions.value;
  offset.value = 0;
  load();
}

// ── trace 详情抽屉 ────────────────────────────────────────
const detailVisible = ref(false);
const detailLoading = ref(false);
const detailTrace = ref<TraceDetail | null>(null);
const detailObservations = ref<ObservationItem[]>([]);

// ── Hermes 内部调用关联 ───────────────────────────────────
// Gateway trace 写入 metadata.last_user_message_hash + gateway_request_time + session_id，
// 后端 /traces/{id}/hermes-correlation 按 session_id + 哈希 + 时间窗口查 Hermes 内层 trace
// 命中时把 Hermes 的 observations 挂到 Gateway trace 详情下方展示
const hermesLoading = ref(false);
const hermesTrace = ref<TraceDetail | null>(null);
const hermesObservations = ref<ObservationItem[]>([]);
const hermesReason = ref<string>("");
const hermesCandidateCount = ref<number | null>(null);
// Hermes 关联区块是否被用户手动折叠/展开过（默认展开）
const hermesSectionExpanded = ref(true);

// reason → 用户可读文案
const HERMES_REASON_LABELS: Record<string, string> = {
  sub_turn_hash_matched: "已关联",
  trace_input_hash_matched: "已关联",
  direct_llm_call: "直接 LLM 调用（/v1/chat/completions），无内部 trace",
  no_matching_hermes_trace: "未找到关联的 Hermes 内部 trace",
  no_correlation_keys_in_gateway_trace: "Gateway trace 缺少关联键（session_id / 哈希）",
  gateway_trace_not_found: "Gateway trace 不存在",
  langfuse_not_configured: "Langfuse 未配置",
  list_traces_failed: "Langfuse 查询失败",
  fetch_failed: "查询失败",
};

async function loadHermesCorrelation(traceId: string) {
  hermesLoading.value = true;
  hermesTrace.value = null;
  hermesObservations.value = [];
  hermesReason.value = "";
  hermesCandidateCount.value = null;
  try {
    const resp = await getHermesCorrelationApi(traceId);
    hermesTrace.value = resp.hermes_trace;
    hermesObservations.value = resp.observations || [];
    hermesReason.value = resp.reason || "";
    hermesCandidateCount.value = resp.candidate_count ?? null;
  } catch {
    hermesReason.value = "fetch_failed";
  } finally {
    hermesLoading.value = false;
  }
}

// Hermes observations 按 (startTime, endTime) 升序排（与 Gateway observations 一致）
const sortedHermesObservations = computed(() => {
  const ts = (t: string | null | undefined) => t ? new Date(t).getTime() : Number.MAX_SAFE_INTEGER;
  return hermesObservations.value.slice().sort((a, b) => {
    const d = ts(a.startTime) - ts(b.startTime);
    return d !== 0 ? d : ts(a.endTime) - ts(b.endTime);
  });
});

// Hermes 内部 LLM 调用次数（仅 GENERATION 类型）
const hermesLlmCallCount = computed(
  () => sortedHermesObservations.value.filter((o) => o.type === "GENERATION").length
);

// 是否显示 Hermes 关联区块：Gateway trace 有关联键，或已命中 Hermes trace
const HERMES_MATCHED_REASONS = new Set([
  "sub_turn_hash_matched",
  "trace_input_hash_matched",
]);
const showHermesSection = computed(() => {
  if (hermesTrace.value) return true;
  if (HERMES_MATCHED_REASONS.has(hermesReason.value)) return true;
  // Gateway trace 有 last_user_message_hash → 该 trace 走双写流程，应展示查询结果（含未匹配）
  const meta = detailTrace.value?.metadata as Record<string, unknown> | undefined;
  return !!(meta && meta.last_user_message_hash);
});

async function openDetail(traceId: string) {
  detailVisible.value = true;
  detailLoading.value = true;
  detailTrace.value = null;
  detailObservations.value = [];
  hermesTrace.value = null;
  hermesObservations.value = [];
  hermesReason.value = "";
  hermesCandidateCount.value = null;
  try {
    const resp = await getTraceDetailApi(traceId);
    detailTrace.value = resp.trace;
    // 按 (startTime, endTime) 升序排，让 #1 是调用链路的第一步
    // Langfuse v3 list_observations 默认按 createdAt DESC 返回（最新在前），
    // 直接渲染会让 #1 是最后一步，视觉上反了
    // 次排序键 endTime：Dify workflow 的 created_at 只精确到秒，Start 和 LLM 节点
    // 的 startTime 可能完全相同（都是 xxx.000Z），同 startTime 时按 endTime 升序
    // （先结束的排前面）——Start 节点 endTime=startTime（0ms），LLM 节点有耗时，
    // 所以 Start 会排到 LLM 前面，符合 workflow 执行顺序
    const ts = (t: string | null | undefined) => t ? new Date(t).getTime() : Number.MAX_SAFE_INTEGER;
    detailObservations.value = (resp.observations || []).slice().sort((a, b) => {
      const d = ts(a.startTime) - ts(b.startTime);
      return d !== 0 ? d : ts(b.endTime) - ts(b.endTime);
    });
    // 自动加载 Hermes 关联（不阻塞详情主信息显示）
    loadHermesCorrelation(traceId);
  } catch (e) {
    ElMessage.error("加载 trace 详情失败");
  } finally {
    detailLoading.value = false;
  }
}

function obsLatencyMs(o: ObservationItem): number | null {
  if (!o.startTime || !o.endTime) return null;
  const s = new Date(o.startTime).getTime();
  const e = new Date(o.endTime).getTime();
  if (isNaN(s) || isNaN(e)) return null;
  const delta = e - s;
  return delta >= 0 ? delta : null;
}

// 列表行 token 展示：
// - 有值时显示数字
// - 无值但有 token_total（Dify workflow 模式）→ "无法获取"（无 input/output 拆分）
// - 完全无 usage 数据 → 0
function rowTokenDisplay(val: number | null | undefined, total: number | null | undefined): string {
  if (val && val > 0) return String(val);
  if (total && total > 0) return "无法获取";
  return "0";
}

function obsTtftMs(o: ObservationItem): number | null {
  if (!o.completionStartTime || !o.startTime) return null;
  const s = new Date(o.startTime).getTime();
  const c = new Date(o.completionStartTime).getTime();
  if (isNaN(s) || isNaN(c)) return null;
  const delta = c - s;
  return delta >= 0 ? delta : null;
}

function obsTokenTotal(o: ObservationItem): number {
  const u = o.usage;
  if (!u) return 0;
  return Number(u.total ?? (u.input ?? 0) + (u.output ?? 0));
}

const totalInputTokens = computed(() =>
  detailObservations.value.reduce((sum, o) => sum + Number(o.usage?.input ?? 0), 0)
);
const totalOutputTokens = computed(() =>
  detailObservations.value.reduce((sum, o) => sum + Number(o.usage?.output ?? 0), 0)
);
// 合计：优先用 observation.usage.total 求和（Dify workflow 只给 total 无 input/output 拆分），
// 没有时回退到 input + output（OpenAI 兼容响应）。
const totalTokens = computed(() => {
  const sumTotal = detailObservations.value.reduce(
    (sum, o) => sum + Number(o.usage?.total ?? 0),
    0
  );
  if (sumTotal > 0) return sumTotal;
  return totalInputTokens.value + totalOutputTokens.value;
});
// 调用次数：只统计 GENERATION observation（真正的 LLM API 调用），
// SPAN 是 workflow 节点或 agent_thought 步骤，不算独立 LLM 调用。
const llmCallCount = computed(
  () => detailObservations.value.filter((o) => o.type === "GENERATION").length
);
// 是否显示"Dify workflow 模式"提示：input/output 都为 0 但 total > 0
const showWorkflowUsageHint = computed(
  () => totalInputTokens.value === 0 && totalOutputTokens.value === 0 && totalTokens.value > 0
);
// 输入/输出 token 展示文本：
// - 有值时显示数字
// - 无值但有 total（Dify workflow 模式）→ "无法获取"（无 input/output 拆分）
// - 完全无 usage 数据 → "—"
const inputTokenDisplay = computed(() => {
  if (totalInputTokens.value > 0) return String(totalInputTokens.value);
  if (totalTokens.value > 0) return "无法获取";
  return "—";
});
const outputTokenDisplay = computed(() => {
  if (totalOutputTokens.value > 0) return String(totalOutputTokens.value);
  if (totalTokens.value > 0) return "无法获取";
  return "—";
});

// 延迟拆分：从 observations 算 TTFT + 平均增量（与后端 _trace_latency_breakdown 对齐）
const detailE2eMs = computed<number | null>(() => {
  if (detailTrace.value?.latency != null) {
    return Math.round(detailTrace.value.latency * 1000);
  }
  return null;
});

const detailTtftMs = computed<number | null>(() => {
  // 找首个 GENERATION observation 的 completionStartTime - startTime
  for (const o of detailObservations.value) {
    if (o.type === "GENERATION" && o.startTime && o.completionStartTime) {
      const s = new Date(o.startTime).getTime();
      const c = new Date(o.completionStartTime).getTime();
      if (!isNaN(s) && !isNaN(c) && c >= s) {
        return c - s;
      }
    }
  }
  return null;
});

const detailAvgIncrementalMs = computed<number | null>(() => {
  const e2e = detailE2eMs.value;
  const ttft = detailTtftMs.value;
  const out = totalOutputTokens.value;
  if (e2e == null || ttft == null || out <= 1) return null;
  const gen = e2e - ttft;
  return gen >= 0 ? Math.round(gen / (out - 1)) : null;
});

function obsNodeColor(o: ObservationItem): string {
  if (o.level === "ERROR") return "bg-red-500";
  if (o.type === "GENERATION") return "bg-blue-500";
  if (o.type === "SPAN") return "bg-green-500";
  return "bg-gray-400";
}

function levelTagType(level: string): string {
  if (level === "ERROR") return "danger";
  if (level === "WARNING") return "warning";
  if (level === "DEBUG") return "info";
  return "";
}

function typeLabel(t: string): string {
  return { SPAN: "Span", GENERATION: "Generation", EVENT: "Event" }[t] || t;
}

function truncateStr(s: string, n: number): string {
  return s.length > n ? s.slice(0, n) + "…" : s;
}

function stringifyVal(v: unknown): string {
  if (v == null) return "";
  if (typeof v === "string") return v;
  try {
    return JSON.stringify(v, null, 2);
  } catch {
    return String(v);
  }
}

// ── 渠道展示 helper ────────────────────────────────────────
// channel_type 取值：web（终端门户）/ wecom / feishu / dingtalk / wecom_bot
const CHANNEL_LABELS: Record<string, string> = {
  web: "Web",
  wecom: "企微",
  feishu: "飞书",
  dingtalk: "钉钉",
  wecom_bot: "企微Bot",
};
const CHANNEL_TAG_TYPES: Record<string, string> = {
  web: "info",
  wecom: "success",
  feishu: "primary",
  dingtalk: "warning",
  wecom_bot: "success",
};
function channelLabel(c: string | null | undefined): string {
  if (!c) return "—";
  return CHANNEL_LABELS[c] || c;
}
function channelTagType(c: string | null | undefined): string {
  if (!c) return "info";
  return CHANNEL_TAG_TYPES[c] || "info";
}

// ── 列管理（用户自定义展示哪些列）────────────────────────────
// 状态存 localStorage，刷新不丢。operation 列固定显示不可隐藏。
const columnDefs = [
  { key: "trace_id", label: "Trace ID" },
  { key: "agent", label: "智能体" },
  { key: "session", label: "会话ID" },
  { key: "enduser", label: "终端用户" },
  { key: "channel", label: "渠道" },
  { key: "api", label: "API" },
  { key: "latency", label: "延迟" },
  { key: "token", label: "Token" },
  { key: "status", label: "状态" },
  { key: "time", label: "时间" },
  { key: "operation", label: "操作" },
];
const DEFAULT_VISIBLE_COLUMNS = [
  "trace_id", "agent", "enduser", "channel", "api",
  "latency", "token", "status", "time", "operation",
];
const COLUMN_STORAGE_KEY = "trace_visible_columns";

function loadVisibleColumns(): string[] {
  try {
    const saved = localStorage.getItem(COLUMN_STORAGE_KEY);
    if (saved) {
      const parsed = JSON.parse(saved);
      if (Array.isArray(parsed) && parsed.length > 0) return parsed;
    }
  } catch {
    // localStorage 解析失败回退默认
  }
  return [...DEFAULT_VISIBLE_COLUMNS];
}

const visibleColumns = ref<string[]>(loadVisibleColumns());

watch(visibleColumns, (v) => {
  try {
    localStorage.setItem(COLUMN_STORAGE_KEY, JSON.stringify(v));
  } catch {
    // localStorage 写入失败（隐私模式等）忽略
  }
}, { deep: true });

function isColumnVisible(key: string): boolean {
  return visibleColumns.value.includes(key);
}
function resetColumns() {
  visibleColumns.value = [...DEFAULT_VISIBLE_COLUMNS];
}
function selectAllColumns() {
  visibleColumns.value = columnDefs.map(c => c.key);
}

onMounted(() => {
  loadAgents();
  load();
  // 来自告警事件列表的跳转：自动打开对应 trace 详情抽屉
  const tid = route.query.trace_id;
  if (typeof tid === "string" && tid) {
    openDetail(tid);
  }
});
</script>

<template>
  <div class="main" v-loading="loading">
    <DocsLink to="monitoring.html#trace" />
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
            @change="() => { offset = 0; load(); }"
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
            v-model="sessionFilter"
            placeholder="会话 ID"
            clearable
            style="width: 220px"
            @keyup.enter="() => { offset = 0; load(); }"
            @clear="() => { offset = 0; load(); }"
          />
          <el-input
            v-model="enduserId"
            placeholder="终端用户 ID"
            clearable
            style="width: 200px"
            @keyup.enter="() => { offset = 0; load(); }"
            @clear="() => { offset = 0; load(); }"
          />
          <el-select
            v-model="channelType"
            clearable
            placeholder="渠道"
            style="width: 140px"
            @change="() => { offset = 0; load(); }"
          >
            <el-option label="Web 门户" value="web" />
            <el-option label="企业微信" value="wecom" />
            <el-option label="飞书" value="feishu" />
            <el-option label="钉钉" value="dingtalk" />
            <el-option label="企微 Bot" value="wecom_bot" />
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
            v-model="minutes"
            placeholder="快速选择"
            style="width: 140px"
            @change="onMinutesChange"
          >
            <el-option label="近 10 分钟" :value="10" />
            <el-option label="近 30 分钟" :value="30" />
            <el-option label="近 1 小时" :value="60" />
            <el-option label="近 24 小时" :value="1440" />
            <el-option label="近 3 天" :value="4320" />
          </el-select>
        </div>
        <div class="flex items-center gap-3 flex-wrap">
          <el-popover trigger="click" placement="bottom-end" :width="200">
            <template #reference>
              <el-button>列管理</el-button>
            </template>
            <div class="text-xs text-gray-500 mb-2">选择要展示的列</div>
            <el-checkbox-group v-model="visibleColumns">
              <div v-for="c in columnDefs" :key="c.key" class="py-1">
                <el-checkbox :label="c.key" :disabled="c.key === 'operation'">{{ c.label }}</el-checkbox>
              </div>
            </el-checkbox-group>
            <div class="mt-2 flex justify-between">
              <el-button link size="small" @click="resetColumns">重置</el-button>
              <el-button link size="small" @click="selectAllColumns">全选</el-button>
            </div>
          </el-popover>
          <el-button @click="resetFilters">重置</el-button>
          <el-button type="primary" @click="() => { offset = 0; load(); }">刷新</el-button>
          <a v-if="langfuseConfigured && langfuseUrl" :href="langfuseUrl" target="_blank" class="text-xs text-blue-500 hover:underline">在 Langfuse 中查看完整版 →</a>
        </div>
      </div>

      <el-row :gutter="12" class="mb-4">
        <el-col :span="8">
          <el-card shadow="never" :body-style="{ padding: '12px 16px' }">
            <div class="text-xs text-gray-400">最近 Trace 数</div>
            <div class="text-2xl font-semibold mt-1">{{ items.length }}</div>
          </el-card>
        </el-col>
        <el-col :span="8">
          <el-card shadow="never" :body-style="{ padding: '12px 16px' }">
            <div class="text-xs text-gray-400">错误数</div>
            <div class="text-2xl font-semibold mt-1" :class="errorCount > 0 ? 'text-red-500' : ''">{{ errorCount }}</div>
          </el-card>
        </el-col>
        <el-col :span="8">
          <el-card shadow="never" :body-style="{ padding: '12px 16px' }">
            <div class="text-xs text-gray-400">平均延迟</div>
            <div class="text-2xl font-semibold mt-1">{{ formatLatency(avgLatency) }}</div>
          </el-card>
        </el-col>
      </el-row>

      <el-alert
        v-if="!langfuseConfigured"
        title="Langfuse 未配置，链路追踪数据不可用"
        type="info"
        :closable="false"
        class="mb-4"
      />

      <el-card shadow="never">
        <el-table :data="items" stripe style="width: 100%">
          <el-table-column v-if="isColumnVisible('trace_id')" label="Trace ID" min-width="160">
            <template #default="{ row }">
              <div class="flex items-center gap-1">
                <el-tooltip :content="row.id" placement="top" :hide-after="0">
                  <span class="font-mono text-xs">{{ row.id.slice(0, 12) }}…</span>
                </el-tooltip>
                <el-tooltip content="复制完整 Trace ID" placement="top" :hide-after="0">
                  <el-icon class="copy-icon" @click.stop="copyTraceId(row.id)">
                    <FileCopyLine />
                  </el-icon>
                </el-tooltip>
              </div>
            </template>
          </el-table-column>
          <el-table-column v-if="isColumnVisible('agent')" label="智能体" min-width="120">
            <template #default="{ row }">
              <el-tooltip :content="row.agent_id || '—'" placement="top" :hide-after="0">
                <span class="text-xs">{{ agentDisplayName(row.agent_id) }}</span>
              </el-tooltip>
            </template>
          </el-table-column>
          <el-table-column v-if="isColumnVisible('session')" label="会话ID" min-width="120">
            <template #default="{ row }">
              <el-tooltip :content="row.session_id || '—'" placement="top" :hide-after="0">
                <span class="font-mono text-xs">{{ row.session_id ? row.session_id.slice(0, 12) + "…" : "—" }}</span>
              </el-tooltip>
            </template>
          </el-table-column>
          <el-table-column v-if="isColumnVisible('enduser')" label="终端用户" min-width="120">
            <template #default="{ row }">
              <el-tooltip :content="row.enduser_id || '—'" placement="top" :hide-after="0">
                <span class="font-mono text-xs">{{ row.enduser_id ? row.enduser_id.slice(0, 8) + "…" : "—" }}</span>
              </el-tooltip>
            </template>
          </el-table-column>
          <el-table-column v-if="isColumnVisible('channel')" label="渠道" min-width="100">
            <template #default="{ row }">
              <el-tag size="small" :type="channelTagType(row.channel_type) as any">{{ channelLabel(row.channel_type) }}</el-tag>
            </template>
          </el-table-column>
          <el-table-column v-if="isColumnVisible('api')" label="API" prop="name" min-width="140" />
          <el-table-column v-if="isColumnVisible('latency')" label="延迟" min-width="120">
            <template #default="{ row }">
              <el-tooltip placement="top" :hide-after="0">
                <template #content>
                  <div class="text-xs leading-relaxed">
                    <div>端到端: {{ formatLatency(row.latency_ms) }}</div>
                    <div>首 Token: {{ formatLatency(row.ttft_ms) }}</div>
                    <div>平均增量: {{ formatLatency(row.avg_incremental_ms) }}</div>
                    <div class="text-gray-400 mt-1">调用 {{ row.observation_count || 0 }} 次</div>
                  </div>
                </template>
                <span class="text-xs font-mono">{{ formatLatency(row.latency_ms) }}</span>
              </el-tooltip>
            </template>
          </el-table-column>
          <el-table-column v-if="isColumnVisible('token')" label="Token" min-width="100">
            <template #default="{ row }">
              <el-tooltip placement="top" :hide-after="0">
                <template #content>
                  <div class="text-xs leading-relaxed">
                    <div>输入: {{ rowTokenDisplay(row.token_input, row.token_total) }}</div>
                    <div>输出: {{ rowTokenDisplay(row.token_output, row.token_total) }}</div>
                    <div class="text-gray-400 mt-1">输入含系统提示+工具定义+历史</div>
                  </div>
                </template>
                <span class="text-xs font-mono">{{ row.token_total || 0 }}</span>
              </el-tooltip>
            </template>
          </el-table-column>
          <el-table-column v-if="isColumnVisible('status')" label="状态" min-width="80">
            <template #default="{ row }">
              <el-tag :type="row.status === 'ok' ? 'success' : 'danger'" size="small">
                {{ row.status === "ok" ? "成功" : "错误" }}
              </el-tag>
            </template>
          </el-table-column>
          <el-table-column v-if="isColumnVisible('time')" label="时间" min-width="160">
            <template #default="{ row }">{{ formatTime(row.created_at) }}</template>
          </el-table-column>
          <el-table-column v-if="isColumnVisible('operation')" label="操作" width="140" fixed="right">
            <template #default="{ row }">
              <el-button type="primary" link size="small" @click="openDetail(row.id)">详情</el-button>
              <el-button
                v-if="langfuseConfigured"
                type="info"
                link
                size="small"
                @click="openInLangfuse(row.id)"
              >Langfuse</el-button>
            </template>
          </el-table-column>
        </el-table>

        <div class="w-full flex justify-end mt-3" v-if="total > limit">
          <el-pagination
            :total="total"
            :page-size="limit"
            :current-page="Math.floor(offset / limit) + 1"
            layout="prev, pager, next"
            @current-change="handlePageChange"
          />
        </div>
      </el-card>
    </div>

    <!-- Trace 详情抽屉 -->
    <el-drawer v-model="detailVisible" title="Trace 详情" size="60%" direction="rtl">
      <div v-loading="detailLoading" class="px-2">
        <template v-if="detailTrace">
          <el-descriptions :column="2" border size="small" class="mb-4">
            <el-descriptions-item label="Trace ID">
              <span class="font-mono text-xs">{{ detailTrace.id }}</span>
            </el-descriptions-item>
            <el-descriptions-item label="API">{{ detailTrace.name }}</el-descriptions-item>
            <el-descriptions-item label="智能体ID">
              <span class="font-mono text-xs">{{ formatAgentId(detailTrace.userId) }}</span>
            </el-descriptions-item>
            <el-descriptions-item label="会话ID">
              <span class="font-mono text-xs">{{ detailTrace.sessionId || "—" }}</span>
            </el-descriptions-item>
            <el-descriptions-item label="终端用户">
              <span class="font-mono text-xs">{{ (detailTrace.metadata as Record<string, unknown>)?.enduser_id as string || "—" }}</span>
            </el-descriptions-item>
            <el-descriptions-item label="渠道">
              <el-tag size="small" :type="channelTagType((detailTrace.metadata as Record<string, unknown>)?.channel_type as string) as any">
                {{ channelLabel((detailTrace.metadata as Record<string, unknown>)?.channel_type as string) }}
              </el-tag>
            </el-descriptions-item>
            <el-descriptions-item label="时间">{{ formatTime(detailTrace.timestamp || detailTrace.createdAt) }}</el-descriptions-item>
            <el-descriptions-item label="端到端延迟">
              {{ detailTrace.latency != null ? formatLatency(detailTrace.latency * 1000) : "—" }}
            </el-descriptions-item>
            <el-descriptions-item label="总成本">
              {{ detailTrace.totalCost != null ? `¥${Number(detailTrace.totalCost).toFixed(4)}` : "—" }}
            </el-descriptions-item>
          </el-descriptions>

          <!-- 延迟拆分卡 -->
          <el-card shadow="never" class="mb-4" :body-style="{ padding: '12px 16px' }">
            <div class="text-xs text-gray-400 mb-2">延迟拆分</div>
            <div class="flex items-center gap-6 flex-wrap">
              <div>
                <span class="text-xs text-gray-500">首 Token (TTFT)</span>
                <div class="font-mono text-sm font-semibold text-blue-600">
                  {{ formatLatency(detailTtftMs) }}
                </div>
              </div>
              <div>
                <span class="text-xs text-gray-500">端到端 (E2E)</span>
                <div class="font-mono text-sm font-semibold">
                  {{ formatLatency(detailE2eMs) }}
                </div>
              </div>
              <div>
                <span class="text-xs text-gray-500">平均增量 / token</span>
                <div class="font-mono text-sm font-semibold text-green-600">
                  {{ formatLatency(detailAvgIncrementalMs) }}
                </div>
              </div>
              <div>
                <span class="text-xs text-gray-500">输出 token</span>
                <div class="font-mono text-sm">{{ totalOutputTokens }}</div>
              </div>
            </div>
            <div class="text-xs text-gray-400 mt-2">
              首 Token = 网关收到上游首个 SSE chunk 的时间；平均增量 = (E2E - TTFT) / (输出 token - 1)
            </div>
          </el-card>

          <!-- Token 构成卡 -->
          <el-card shadow="never" class="mb-4" :body-style="{ padding: '12px 16px' }">
            <div class="text-xs text-gray-400 mb-2">Token 构成</div>
            <div class="flex items-center gap-6 flex-wrap">
              <div>
                <span class="text-xs text-gray-500">输入</span>
                <span class="ml-2 font-mono text-sm">{{ inputTokenDisplay }}</span>
              </div>
              <div>
                <span class="text-xs text-gray-500">输出</span>
                <span class="ml-2 font-mono text-sm">{{ outputTokenDisplay }}</span>
              </div>
              <div>
                <span class="text-xs text-gray-500">合计</span>
                <span class="ml-2 font-mono text-sm font-semibold">{{ totalTokens }}</span>
              </div>
              <div>
                <span class="text-xs text-gray-500">模型调用次数</span>
                <span class="ml-2 font-mono text-sm">{{ llmCallCount }}</span>
              </div>
            </div>
            <div class="text-xs text-gray-400 mt-2">
              <template v-if="showWorkflowUsageHint">
                Dify workflow 模式只返回 total_tokens（无 input/output 拆分），输入/输出显示为 —
              </template>
              <template v-else>
                输入 token 包含系统提示词、工具定义、历史消息，不只用户当前输入
              </template>
            </div>
          </el-card>

          <div class="mb-3" v-if="detailTrace.metadata && Object.keys(detailTrace.metadata).length">
            <div class="text-sm font-medium mb-1">Metadata</div>
            <el-card shadow="never" :body-style="{ padding: '8px 12px' }">
              <pre class="text-xs font-mono whitespace-pre-wrap break-all m-0">{{ JSON.stringify(detailTrace.metadata, null, 2) }}</pre>
            </el-card>
          </div>

          <div class="mb-2 flex items-center justify-between">
            <div class="text-sm font-medium">调用链路（{{ detailObservations.length }}）</div>
            <el-button
              v-if="langfuseUrl"
              type="primary"
              link
              size="small"
              @click="openInLangfuse(detailTrace.id)"
            >在 Langfuse 中查看 →</el-button>
          </div>

          <el-empty v-if="!detailObservations.length" description="无 observation" :image-size="60" />

          <div v-else class="relative pl-4">
            <!-- 时间线竖线 -->
            <div class="absolute left-1 top-2 bottom-2 w-px bg-gray-200"></div>
            <div v-for="(o, idx) in detailObservations" :key="o.id" class="relative mb-3">
              <!-- 节点圆点 -->
              <span
                class="absolute -left-3.5 top-3 w-2.5 h-2.5 rounded-full border-2 border-white"
                :class="obsNodeColor(o)"
              ></span>
              <el-card shadow="never" :body-style="{ padding: '10px 14px' }">
                <div class="flex items-center gap-2 mb-2 flex-wrap">
                  <span class="text-xs text-gray-400">#{{ idx + 1 }}</span>
                  <el-tag size="small" :type="levelTagType(o.level) as any">{{ o.level }}</el-tag>
                  <el-tag type="info" size="small">{{ typeLabel(o.type) }}</el-tag>
                  <span class="font-mono text-xs text-gray-500">{{ o.name || o.id.slice(0, 12) }}</span>
                  <span v-if="o.model" class="text-xs text-gray-500">model: {{ o.model }}</span>
                </div>
                <div class="flex flex-wrap gap-4 text-xs text-gray-600 mb-2">
                  <span>开始: {{ formatTime(o.startTime) }}</span>
                  <span>结束: {{ formatTime(o.endTime) }}</span>
                  <span>耗时: {{ formatLatency(obsLatencyMs(o)) }}</span>
                  <span v-if="obsTtftMs(o) != null">
                    首 Token: {{ formatLatency(obsTtftMs(o)) }}
                  </span>
                  <span v-if="o.usage">
                    Token: {{ obsTokenTotal(o) }}
                    <span class="text-gray-400">(in={{ o.usage.input ?? 0 }}, out={{ o.usage.output ?? 0 }})</span>
                  </span>
                  <span v-if="o.calculatedTotalCost != null">成本: ¥{{ Number(o.calculatedTotalCost).toFixed(4) }}</span>
                </div>
                <el-collapse v-if="o.input != null || o.output != null">
                  <el-collapse-item v-if="o.input != null" title="Input" name="in">
                    <pre class="text-xs font-mono whitespace-pre-wrap break-all bg-gray-50 p-2 rounded m-0 max-h-60 overflow-auto">{{ truncateStr(stringifyVal(o.input), 4000) }}</pre>
                  </el-collapse-item>
                  <el-collapse-item v-if="o.output != null" title="Output" name="out">
                    <pre class="text-xs font-mono whitespace-pre-wrap break-all bg-gray-50 p-2 rounded m-0 max-h-60 overflow-auto">{{ truncateStr(stringifyVal(o.output), 4000) }}</pre>
                  </el-collapse-item>
                </el-collapse>
                <div v-else class="text-xs text-gray-400 italic">
                  无 input/output（Dify workflow 的 LLM/Code 节点不在 node 事件里上报 input/output，仅 text_chunk 流携带文本）
                </div>
              </el-card>
            </div>
          </div>

          <!-- Hermes 内部调用关联区块 -->
          <div v-if="showHermesSection" class="mt-4">
            <div class="flex items-center justify-between mb-2">
              <div class="flex items-center gap-2">
                <span class="text-sm font-medium">Hermes 内部调用</span>
                <el-tag v-if="hermesReason" size="small" :type="HERMES_MATCHED_REASONS.has(hermesReason) ? 'success' : 'info'">
                  {{ HERMES_REASON_LABELS[hermesReason] || hermesReason }}
                </el-tag>
                <span v-if="hermesTrace" class="text-xs text-gray-500">
                  {{ sortedHermesObservations.length }} 条 observation · {{ hermesLlmCallCount }} 次 LLM 调用
                </span>
                <span v-if="hermesCandidateCount != null && hermesReason === 'no_matching_hermes_trace'" class="text-xs text-gray-400">
                  候选 trace {{ hermesCandidateCount }} 条均不匹配
                </span>
              </div>
              <div class="flex items-center gap-2">
                <el-button
                  v-if="hermesTrace && langfuseUrl"
                  type="primary"
                  link
                  size="small"
                  @click="openInLangfuse(hermesTrace.id)"
                >在 Langfuse 中查看 Hermes trace →</el-button>
                <el-button
                  link
                  size="small"
                  @click="hermesSectionExpanded = !hermesSectionExpanded"
                >{{ hermesSectionExpanded ? "收起" : "展开" }}</el-button>
              </div>
            </div>

            <div v-if="hermesLoading" v-loading="true" class="h-16"></div>

            <div v-else-if="hermesSectionExpanded">
              <el-empty
                v-if="hermesReason && !HERMES_MATCHED_REASONS.has(hermesReason)"
                :description="HERMES_REASON_LABELS[hermesReason] || hermesReason"
                :image-size="60"
              />
              <el-empty
                v-else-if="!sortedHermesObservations.length"
                description="Hermes trace 无 observation"
                :image-size="60"
              />
              <div v-else class="relative pl-4">
                <!-- 时间线竖线 -->
                <div class="absolute left-1 top-2 bottom-2 w-px bg-gray-300 border-l border-dashed border-gray-300"></div>
                <div v-for="(o, idx) in sortedHermesObservations" :key="o.id" class="relative mb-3">
                  <span
                    class="absolute -left-3.5 top-3 w-2.5 h-2.5 rounded-full border-2 border-white"
                    :class="obsNodeColor(o)"
                  ></span>
                  <el-card shadow="never" :body-style="{ padding: '10px 14px' }" class="bg-gray-50">
                    <div class="flex items-center gap-2 mb-2 flex-wrap">
                      <span class="text-xs text-gray-400">H{{ idx + 1 }}</span>
                      <el-tag size="small" :type="levelTagType(o.level) as any">{{ o.level }}</el-tag>
                      <el-tag type="info" size="small">{{ typeLabel(o.type) }}</el-tag>
                      <span class="font-mono text-xs text-gray-500">{{ o.name || o.id.slice(0, 12) }}</span>
                      <span v-if="o.model" class="text-xs text-gray-500">model: {{ o.model }}</span>
                    </div>
                    <div class="flex flex-wrap gap-4 text-xs text-gray-600 mb-2">
                      <span>开始: {{ formatTime(o.startTime) }}</span>
                      <span>结束: {{ formatTime(o.endTime) }}</span>
                      <span>耗时: {{ formatLatency(obsLatencyMs(o)) }}</span>
                      <span v-if="obsTtftMs(o) != null">首 Token: {{ formatLatency(obsTtftMs(o)) }}</span>
                      <span v-if="o.usage">
                        Token: {{ obsTokenTotal(o) }}
                        <span class="text-gray-400">(in={{ o.usage.input ?? 0 }}, out={{ o.usage.output ?? 0 }})</span>
                      </span>
                      <span v-if="o.calculatedTotalCost != null">成本: ¥{{ Number(o.calculatedTotalCost).toFixed(4) }}</span>
                    </div>
                    <el-collapse v-if="o.input != null || o.output != null">
                      <el-collapse-item v-if="o.input != null" title="Input" name="in">
                        <pre class="text-xs font-mono whitespace-pre-wrap break-all bg-white p-2 rounded m-0 max-h-60 overflow-auto">{{ truncateStr(stringifyVal(o.input), 4000) }}</pre>
                      </el-collapse-item>
                      <el-collapse-item v-if="o.output != null" title="Output" name="out">
                        <pre class="text-xs font-mono whitespace-pre-wrap break-all bg-white p-2 rounded m-0 max-h-60 overflow-auto">{{ truncateStr(stringifyVal(o.output), 4000) }}</pre>
                      </el-collapse-item>
                    </el-collapse>
                  </el-card>
                </div>
              </div>
            </div>
          </div>
        </template>
        <el-empty v-else-if="!detailLoading" description="无详情数据" />
      </div>
    </el-drawer>
  </div>
</template>

<style scoped>
.main {
  padding: 20px;
}
.welcome {
  max-width: 1400px;
  margin: 0 auto;
}
.copy-icon {
  cursor: pointer;
  color: var(--el-text-color-placeholder);
  font-size: 13px;
}
.copy-icon:hover {
  color: var(--el-color-primary);
}
</style>
