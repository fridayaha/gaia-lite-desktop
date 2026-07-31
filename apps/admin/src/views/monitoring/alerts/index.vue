<script setup lang="ts">
import DocsLink from "@/components/DocsLink/index.vue";
import { ref, onMounted, computed, watch } from "vue";
import { useRouter } from "vue-router";
import { ElMessage, ElMessageBox } from "element-plus";
import {
  getAlertEventsApi,
  acknowledgeAlertEventApi,
  getAlertRulesApi,
  updateAlertRuleApi,
  getAlertChannelsApi,
  createAlertChannelApi,
  updateAlertChannelApi,
  deleteAlertChannelApi,
  type AlertEventItem,
  type AlertEventStatus,
  type AlertRuleItem,
  type AlertRuleCategory,
  type AlertRuleType,
  type AlertChannelType,
  type AlertChannelItem,
  type AlertChannelConfig,
  RULE_CATEGORY_LABEL,
  RULE_TYPE_LABEL,
  RULE_TYPE_UNIT,
  RULE_TYPES_NO_THRESHOLD,
  RULE_TYPES_INVERTED,
  RULE_TYPES_BY_CATEGORY,
  RULE_TYPE_CATEGORY
} from "@/api/manager/observability";
import { getInstancesApi } from "@/api/manager/agentInstances";

const loading = ref(false);
const events = ref<AlertEventItem[]>([]);
const router = useRouter();
// 智能体 ID → name 映射（用于「范围」列展示名称）
const agentOptions = ref<{ id: string; name: string }[]>([]);
const total = ref(0);
const currentPage = ref(1);
const pageSize = ref(10);
const stats = ref({
  critical: 0,
  warning: 0,
  firing: 0,
  resolved: 0,
  acknowledged: 0
});
const langfuseConfigured = ref(true);
const severityFilter = ref<string>("");
const categoryFilter = ref<string>("");  // 5 大类过滤，空表示全部
const ruleTypeFilter = ref<string>("");  // 具体类型过滤，空表示全部（依赖分类）
const statusFilter = ref<string>("firing");  // 默认选中「触发中」—— 用户最关心当前活跃异常

// ── 告警规则配置 ──
const ruleDrawerVisible = ref(false);
const ruleLoading = ref(false);
const rules = ref<AlertRuleItem[]>([]);
const activeCategoryTab = ref<AlertRuleCategory>("tracing");

// 5 大类顺序（与后端 category 取值一致）
const RULE_CATEGORY_ORDER: AlertRuleCategory[] = [
  "tracing",
  "resource",
  "service_health",
  "usage",
  "call_analysis"
];

// 按分类分组的规则
const rulesByCategory = computed(() => {
  const map: Record<AlertRuleCategory, AlertRuleItem[]> = {
    tracing: [],
    resource: [],
    service_health: [],
    usage: [],
    call_analysis: []
  };
  for (const r of rules.value) {
    if (map[r.category]) map[r.category].push(r);
  }
  return map;
});

// ── 告警渠道配置（独立抽屉） ──
const channelDrawerVisible = ref(false);
const channelLoading = ref(false);
const channels = ref<AlertChannelItem[]>([]);
const showCreateChannelForm = ref(false);
const newChannel = ref({
  name: "",
  channel_type: "feishu" as AlertChannelType,
  config: { webhook_url: "" } as AlertChannelConfig,
  subscribed_all: false,
  subscribed_rule_ids: [] as string[],
  enabled: true
});

// 渠道类型选项
const channelTypeOptions: { label: string; value: AlertChannelType }[] = [
  { label: "飞书", value: "feishu" },
  { label: "钉钉", value: "dingtalk" },
  { label: "企业微信", value: "wecom" },
  { label: "邮件", value: "email" }
];
const channelTypeLabel: Record<AlertChannelType, string> = {
  feishu: "飞书",
  dingtalk: "钉钉",
  wecom: "企业微信",
  email: "邮件"
};

// 「全部规则」特殊选项 ID（多选列表头部固定项）
const ALL_RULES_ID = "__all__";

// 渠道订阅规则多选：按 category 分组（el-option-group）
// 顶部插入「全部规则」特殊项
const ruleSelectGroups = computed(() => {
  const groups: { category: AlertRuleCategory; label: string; options: { id: string; name: string }[] }[] = [];
  for (const cat of RULE_CATEGORY_ORDER) {
    const list = rulesByCategory.value[cat] || [];
    if (!list.length) continue;
    groups.push({
      category: cat,
      label: `${RULE_CATEGORY_LABEL[cat]} (${list.length})`,
      options: list.map(r => ({ id: r.id, name: r.name }))
    });
  }
  return groups;
});

// 订阅规则数文本（用于渠道列表展示）
function subscribedRuleNames(ch: AlertChannelItem): string {
  if (ch.subscribed_all) return "全部规则";
  if (!ch.subscribed_rule_ids.length) return "—";
  const names = ch.subscribed_rule_ids
    .map(rid => rules.value.find(r => r.id === rid)?.name)
    .filter(Boolean) as string[];
  return names.length ? names.join("、") : "—";
}

// 「全部」toggle：勾选「全部」→ 清空其他 + subscribed_all=true；
// 取消「全部」→ subscribed_all=false。
function onSubscribedRuleIdsChange(ids: string[], target: { subscribed_all: boolean; subscribed_rule_ids: string[] }) {
  if (ids.includes(ALL_RULES_ID)) {
    if (ids.length > 1) {
      // 勾了「全部」时又同时勾了其他 → 只保留「全部」
      target.subscribed_rule_ids = [ALL_RULES_ID];
      target.subscribed_all = true;
    } else {
      target.subscribed_all = true;
    }
  } else {
    target.subscribed_all = false;
  }
}

// 提交渠道前清洗：subscribed_all=true → subscribed_rule_ids 传空数组；
// 否则去掉 ALL_RULES_ID（不应出现，兜底）
function cleanChannelPayload(channel: {
  subscribed_all: boolean;
  subscribed_rule_ids: string[];
}): { subscribed_all: boolean; subscribed_rule_ids: string[] } {
  if (channel.subscribed_all) {
    return { subscribed_all: true, subscribed_rule_ids: [] };
  }
  return {
    subscribed_all: false,
    subscribed_rule_ids: channel.subscribed_rule_ids.filter(id => id !== ALL_RULES_ID)
  };
}

// 校验渠道 config：webhook 必须 http(s)://，email to 每项含 @
function validateChannelConfig(channelType: AlertChannelType, config: AlertChannelConfig): string | null {
  if (channelType === "email") {
    const to = config.to || [];
    if (!to.length) return "邮件渠道：收件人不能为空";
    for (const addr of to) {
      if (!addr.includes("@")) return `邮箱地址无效 — ${addr}`;
    }
  } else {
    const url = config.webhook_url || "";
    if (!url.startsWith("http://") && !url.startsWith("https://")) {
      return `${channelTypeLabel[channelType]}：webhook URL 必须以 http(s):// 开头`;
    }
  }
  return null;
}

// 切换渠道类型时清理对立字段：feishu/dingtalk/wecom 用 webhook_url，email 用 to
function onChannelTypeChange(ch: { channel_type: AlertChannelType; config: AlertChannelConfig }) {
  if (ch.channel_type === "email") {
    ch.config = { to: [] };
  } else {
    ch.config = { webhook_url: "" };
  }
}

function resetNewChannel() {
  newChannel.value = {
    name: "",
    channel_type: "feishu",
    config: { webhook_url: "" },
    subscribed_all: false,
    subscribed_rule_ids: [],
    enabled: true
  };
}

const criticalCount = computed(() => stats.value.critical);
const warningCount = computed(() => stats.value.warning);
const firingCount = computed(() => stats.value.firing);
const resolvedCount = computed(() => stats.value.resolved);
const acknowledgedCount = computed(() => stats.value.acknowledged);

// 把分类/类型过滤展开为 rule_type 列表传给后端
// - 选了具体 rule_type → 只传 [ruleType]
// - 选了分类 → 传该分类下所有 rule_type
// - 都没选 → undefined
function computeRuleTypes(): string | undefined {
  if (ruleTypeFilter.value) return ruleTypeFilter.value;
  if (categoryFilter.value) {
    const list = RULE_TYPES_BY_CATEGORY[categoryFilter.value as AlertRuleCategory];
    return list ? list.join(",") : undefined;
  }
  return undefined;
}

async function load() {
  loading.value = true;
  try {
    const resp = await getAlertEventsApi({
      pageSize: pageSize.value,
      currentPage: currentPage.value,
      severity: severityFilter.value || undefined,
      status: statusFilter.value || undefined,
      rule_types: computeRuleTypes()
    });
    const data = resp.data;
    events.value = data?.list || [];
    total.value = data?.total || 0;
    stats.value = data?.stats || {
      critical: 0,
      warning: 0,
      firing: 0,
      resolved: 0,
      acknowledged: 0
    };
    langfuseConfigured.value = data?.langfuse_configured ?? true;
  } catch {
    events.value = [];
    total.value = 0;
    stats.value = { critical: 0, warning: 0, firing: 0, resolved: 0, acknowledged: 0 };
  } finally {
    loading.value = false;
  }
}

// severityFilter / statusFilter / categoryFilter / ruleTypeFilter 改变时 reset 到第 1 页 + 重新 load
watch(severityFilter, () => {
  currentPage.value = 1;
  load();
});
watch(statusFilter, () => {
  currentPage.value = 1;
  load();
});
// 选分类时清掉具体类型（避免选了别的分类下的类型不一致）
watch(categoryFilter, () => {
  ruleTypeFilter.value = "";
  currentPage.value = 1;
  load();
});
watch(ruleTypeFilter, () => {
  currentPage.value = 1;
  load();
});

// 类型 select 选项：选了分类时只显示该分类下 rule_type；否则显示所有（按分类 group）
const ruleTypeOptions = computed(() => {
  if (categoryFilter.value) {
    const cat = categoryFilter.value as AlertRuleCategory;
    return [{ category: cat, label: RULE_CATEGORY_LABEL[cat], options: RULE_TYPES_BY_CATEGORY[cat] || [] }];
  }
  return RULE_CATEGORY_ORDER.map(cat => ({
    category: cat,
    label: RULE_CATEGORY_LABEL[cat],
    options: RULE_TYPES_BY_CATEGORY[cat] || []
  }));
});

function handlePageChange(page: number) {
  currentPage.value = page;
  load();
}

function handleSizeChange(size: number) {
  pageSize.value = size;
  currentPage.value = 1;
  load();
}

async function loadRules() {
  ruleLoading.value = true;
  try {
    rules.value = await getAlertRulesApi();
  } catch {
    rules.value = [];
  } finally {
    ruleLoading.value = false;
  }
}

function openRuleDrawer() {
  ruleDrawerVisible.value = true;
  loadRules();
}

async function saveRule(rule: AlertRuleItem) {
  try {
    await updateAlertRuleApi(rule.id, {
      name: rule.name,
      threshold: rule.threshold,
      enabled: rule.enabled,
      severity: rule.severity,
      description: rule.description
    });
    ElMessage.success("规则已保存");
    load();
  } catch (e: any) {
    ElMessage.error(e?.response?.data?.detail || "保存失败");
    loadRules();
  }
}

// ── 渠道操作 ──

async function loadChannels() {
  channelLoading.value = true;
  try {
    channels.value = await getAlertChannelsApi();
  } catch {
    channels.value = [];
  } finally {
    channelLoading.value = false;
  }
}

function openChannelDrawer() {
  channelDrawerVisible.value = true;
  // 渠道列表依赖规则列表（展示订阅规则名），先拉规则
  loadRules().then(loadChannels);
}

async function saveChannel(ch: AlertChannelItem) {
  const err = validateChannelConfig(ch.channel_type, ch.config);
  if (err) {
    ElMessage.warning(err);
    return;
  }
  const cleaned = cleanChannelPayload(ch);
  try {
    await updateAlertChannelApi(ch.id, {
      name: ch.name,
      channel_type: ch.channel_type,
      config: ch.config,
      subscribed_all: cleaned.subscribed_all,
      subscribed_rule_ids: cleaned.subscribed_rule_ids,
      enabled: ch.enabled
    });
    ElMessage.success("渠道已保存");
    loadChannels();
  } catch (e: any) {
    ElMessage.error(e?.response?.data?.detail || "保存失败");
    loadChannels();
  }
}

async function removeChannel(ch: AlertChannelItem) {
  try {
    await ElMessageBox.confirm(`确认删除渠道「${ch.name}」？`, "提示", { type: "warning" });
  } catch {
    return;
  }
  try {
    await deleteAlertChannelApi(ch.id);
    ElMessage.success("渠道已删除");
    loadChannels();
  } catch (e: any) {
    ElMessage.error(e?.response?.data?.detail || "删除失败");
  }
}

async function createChannel() {
  if (!newChannel.value.name.trim()) {
    ElMessage.warning("请填写渠道名称");
    return;
  }
  const err = validateChannelConfig(newChannel.value.channel_type, newChannel.value.config);
  if (err) {
    ElMessage.warning(err);
    return;
  }
  const cleaned = cleanChannelPayload(newChannel.value);
  try {
    await createAlertChannelApi({
      name: newChannel.value.name,
      channel_type: newChannel.value.channel_type,
      config: newChannel.value.config,
      subscribed_all: cleaned.subscribed_all,
      subscribed_rule_ids: cleaned.subscribed_rule_ids,
      enabled: newChannel.value.enabled
    });
    ElMessage.success("渠道已创建");
    showCreateChannelForm.value = false;
    resetNewChannel();
    loadChannels();
  } catch (e: any) {
    ElMessage.error(e?.response?.data?.detail || "创建失败");
  }
}

function formatTime(ts: string | null): string {
  if (!ts) return "—";
  try {
    return new Date(ts).toLocaleString("zh-CN", { hour12: false });
  } catch {
    return ts;
  }
}

// 事件列表的 type 文本（兜底用 RULE_TYPE_LABEL）
function typeText(t: string): string {
  return (RULE_TYPE_LABEL as Record<string, string>)[t] || t;
}

// 从 rule_type 反查 category（AlertEvent 表不存 category，前端反查）
function eventCategory(a: AlertEventItem): AlertRuleCategory {
  return (RULE_TYPE_CATEGORY as Record<string, AlertRuleCategory>)[a.rule_type] || "tracing";
}

// 按事件类别返回跳转目标。tracing 必须有 trace_id；
// usage/call_analysis 的 cluster / global 是聚合级，不带 agent_id 参数。
function viewDetailTarget(a: AlertEventItem): { path: string; query: Record<string, string> } | null {
  const cat = eventCategory(a);
  const isSpecificAgent = (id: string | null | undefined) =>
    !!id && id !== "cluster" && id !== "global";
  switch (cat) {
    case "tracing":
      if (!a.trace_id) return null;
      return { path: "/monitoring/trace", query: { trace_id: a.trace_id } };
    case "resource":
      return { path: "/monitoring/resources", query: {} };
    case "service_health":
      return { path: "/monitoring/service-health", query: {} };
    case "usage":
      return {
        path: "/monitoring/usage",
        query: isSpecificAgent(a.agent_id) ? { agent_id: a.agent_id } : {}
      };
    case "call_analysis":
      return {
        path: "/monitoring/calls",
        query: isSpecificAgent(a.agent_id) ? { agent_id: a.agent_id } : {}
      };
    default:
      return null;
  }
}

function viewDetail(a: AlertEventItem) {
  const t = viewDetailTarget(a);
  if (!t) return;
  router.push(t);
}

function viewDetailLabel(a: AlertEventItem): string {
  const cat = eventCategory(a);
  return cat === "tracing" ? "查看 trace"
    : cat === "resource" ? "查看资源"
    : cat === "service_health" ? "查看健康"
    : cat === "usage" ? "查看用量"
    : cat === "call_analysis" ? "查看调用"
    : "查看";
}

// agent_id 显示（service_health 时是服务名，其他类可能空）
// 「范围」列：按事件类别给出告警作用的目标实体文本
// - resource 固定集群级（agent_id == "cluster"）
// - service_health 是服务名
// - usage / call_analysis 的 agent_id == "global" 时是全局累积值，否则是某智能体
// - tracing 是触发该 trace 的智能体
// 智能体 ID 在映射表命中时显示「名称 (ID前8位)」，否则回退「智能体: <前8位>」
function scopeLabel(a: AlertEventItem): string {
  const cat = eventCategory(a);
  const id = a.agent_id;
  if (cat === "resource") return "集群";
  if (cat === "service_health") return id ? `服务: ${id}` : "—";
  if (cat === "usage" || cat === "call_analysis") {
    if (!id) return "—";
    if (id === "global") return "全局";
    return agentDisplayName(id);
  }
  if (cat === "tracing") return id ? agentDisplayName(id) : "—";
  return id ? agentDisplayName(id) : "—";
}

function agentDisplayName(id: string): string {
  const found = agentOptions.value.find(o => o.id === id);
  return found ? `智能体: ${found.name} (${id.slice(0, 8)})` : `智能体: ${id.slice(0, 8)}`;
}

const severityColor: Record<string, string> = {
  critical: "#f56c6c",
  warning: "#e6a23c"
};

function severityTagType(s: string): "danger" | "warning" | "info" {
  return s === "critical" ? "danger" : s === "warning" ? "warning" : "info";
}

// ── 状态机 helpers（0.8.66） ──
function statusLabel(s: AlertEventStatus | string): string {
  return s === "firing" ? "触发中" : s === "resolved" ? "已恢复" : s === "acknowledged" ? "已确认" : s;
}

function statusTagType(s: AlertEventStatus | string): "danger" | "success" | "info" {
  return s === "firing" ? "danger" : s === "resolved" ? "success" : "info";
}

// 持续时长：firing/acknowledged 用 last_seen_at - created_at；resolved 用 resolved_at - created_at
function durationText(a: AlertEventItem): string {
  if (!a.created_at) return "—";
  const start = new Date(a.created_at).getTime();
  const endTs = a.status === "resolved" ? a.resolved_at : a.last_seen_at;
  if (!endTs) return "—";
  const end = new Date(endTs).getTime();
  const diffMs = end - start;
  if (diffMs < 0) return "—";
  const minutes = Math.floor(diffMs / 60000);
  if (minutes < 60) return `${minutes} 分钟`;
  const hours = Math.floor(minutes / 60);
  const mins = minutes % 60;
  if (hours < 24) return `${hours} 小时 ${mins} 分钟`;
  const days = Math.floor(hours / 24);
  return `${days} 天 ${hours % 24} 小时`;
}

// 是否显示「确认」按钮：只有 B 类（tracing）+ C 类（usage）才需要人工确认。
// A 类（resource/service_health/call_analysis）可自动恢复，不需要人工确认。
function canAcknowledge(a: AlertEventItem): boolean {
  if (a.status !== "firing") return false;
  const cat = eventCategory(a);
  return cat === "tracing" || cat === "usage";
}

async function acknowledgeEvent(a: AlertEventItem) {
  try {
    await ElMessageBox.confirm(
      `确认告警事件「${a.rule_name}」？确认后不再重复通知该告警（直到事件恢复或重新触发）。`,
      "确认告警",
      { type: "warning" }
    );
  } catch {
    return;
  }
  try {
    await acknowledgeAlertEventApi(a.id);
    ElMessage.success("已确认");
    load();
  } catch (e: any) {
    ElMessage.error(e?.response?.data?.detail || "确认失败");
  }
}

async function loadAgents() {
  try {
    const resp = await getInstancesApi({ page: 1, page_size: 100 });
    agentOptions.value = (resp.items || []).map(i => ({ id: i.id, name: i.name }));
  } catch {
    agentOptions.value = [];
  }
}

onMounted(() => {
  loadAgents();
  load();
});
</script>

<template>
  <div class="main" v-loading="loading">
    <DocsLink to="monitoring.html#alerts" />
    <div class="welcome">
      <div class="w-full flex flex-wrap items-center justify-between mb-4 gap-3">
        <div class="flex items-center gap-3 flex-wrap">
          <el-select v-model="severityFilter" placeholder="按严重级别过滤" clearable style="width: 160px">
            <el-option label="严重" value="critical" />
            <el-option label="警告" value="warning" />
          </el-select>
          <el-select v-model="categoryFilter" placeholder="按分类过滤" clearable style="width: 160px">
            <el-option v-for="cat in RULE_CATEGORY_ORDER" :key="cat" :label="RULE_CATEGORY_LABEL[cat]" :value="cat" />
          </el-select>
          <el-select v-model="ruleTypeFilter" placeholder="按类型过滤" clearable style="width: 180px">
            <el-option-group v-for="grp in ruleTypeOptions" :key="grp.category" :label="grp.label">
              <el-option v-for="t in grp.options" :key="t" :label="RULE_TYPE_LABEL[t]" :value="t" />
            </el-option-group>
          </el-select>
          <el-button type="primary" @click="load">刷新</el-button>
        </div>
        <div class="flex items-center gap-3 flex-wrap">
          <el-button type="primary" plain @click="openRuleDrawer">告警规则</el-button>
          <el-button type="primary" plain @click="openChannelDrawer">告警渠道</el-button>
        </div>
      </div>

      <el-alert
        v-if="!langfuseConfigured"
        title="Langfuse 未配置，链路追踪类异常告警不可用；其他 4 类（资源/服务健康/用量/调用分析）不受影响"
        type="info"
        :closable="false"
        class="mb-4"
      />

      <el-row :gutter="12" class="mb-4">
        <el-col :span="8">
          <el-card shadow="never" :body-style="{ padding: '12px 16px' }">
            <div class="text-xs text-gray-400">触发中异常</div>
            <div class="text-2xl font-semibold mt-1">{{ firingCount }}</div>
          </el-card>
        </el-col>
        <el-col :span="8">
          <el-card shadow="never" :body-style="{ padding: '12px 16px' }">
            <div class="text-xs text-gray-400">严重</div>
            <div class="text-2xl font-semibold mt-1 text-red-500">{{ criticalCount }}</div>
          </el-card>
        </el-col>
        <el-col :span="8">
          <el-card shadow="never" :body-style="{ padding: '12px 16px' }">
            <div class="text-xs text-gray-400">警告</div>
            <div class="text-2xl font-semibold mt-1 text-yellow-500">{{ warningCount }}</div>
          </el-card>
        </el-col>
      </el-row>

      <el-card shadow="never">
        <template #header>
          <div class="flex items-center justify-between flex-wrap gap-2">
            <span class="card-title text-sm">异常事件</span>
            <el-radio-group v-model="statusFilter" size="small">
              <el-radio-button label="">全部 {{ firingCount + resolvedCount + acknowledgedCount }}</el-radio-button>
              <el-radio-button label="firing">触发中 {{ firingCount }}</el-radio-button>
              <el-radio-button label="resolved">已恢复 {{ resolvedCount }}</el-radio-button>
              <el-radio-button label="acknowledged">已确认 {{ acknowledgedCount }}</el-radio-button>
            </el-radio-group>
          </div>
        </template>
        <el-empty v-if="!events.length" description="暂无异常" :image-size="60" />
        <el-table
          v-else
          :data="events"
          stripe
          size="small"
          style="width: 100%"
        >
          <el-table-column label="时间" width="160">
            <template #default="{ row }">
              <span class="text-xs text-gray-500">{{ formatTime(row.created_at) }}</span>
            </template>
          </el-table-column>
          <el-table-column label="持续时长" width="140">
            <template #default="{ row }">
              <span
                class="text-xs"
                :class="row.status === 'resolved' ? 'text-gray-400' : 'text-gray-600'"
              >{{ durationText(row) }}</span>
            </template>
          </el-table-column>
          <el-table-column label="级别" width="80">
            <template #default="{ row }">
              <el-tag :type="severityTagType(row.severity)" size="small">
                {{ row.severity === "critical" ? "严重" : "警告" }}
              </el-tag>
            </template>
          </el-table-column>
          <el-table-column label="状态" width="100">
            <template #default="{ row }">
              <el-tag :type="statusTagType(row.status)" size="small">
                {{ statusLabel(row.status) }}
              </el-tag>
            </template>
          </el-table-column>
          <el-table-column label="分类" width="100">
            <template #default="{ row }">
              <el-tag type="info" size="small" effect="plain">{{ RULE_CATEGORY_LABEL[eventCategory(row) as AlertRuleCategory] }}</el-tag>
            </template>
          </el-table-column>
          <el-table-column label="类型" width="140">
            <template #default="{ row }">
              <el-tag type="info" size="small">{{ typeText(row.rule_type) }}</el-tag>
            </template>
          </el-table-column>
          <el-table-column label="范围" width="200" show-overflow-tooltip>
            <template #default="{ row }">
              <span class="font-mono text-xs text-gray-500">{{ scopeLabel(row) }}</span>
            </template>
          </el-table-column>
          <el-table-column label="详情" min-width="280" show-overflow-tooltip>
            <template #default="{ row }">
              <span class="text-sm text-gray-700">{{ row.message }}</span>
            </template>
          </el-table-column>
          <el-table-column label="操作" width="160" fixed="right">
            <template #default="{ row }">
              <el-button
                v-if="canAcknowledge(row)"
                type="primary"
                link
                size="small"
                @click="acknowledgeEvent(row)"
              >确认</el-button>
              <el-button
                v-if="viewDetailTarget(row)"
                type="primary"
                link
                size="small"
                @click="viewDetail(row)"
              >{{ viewDetailLabel(row) }}</el-button>
              <span
                v-if="!canAcknowledge(row) && !viewDetailTarget(row)"
                class="text-xs text-gray-300"
              >—</span>
            </template>
          </el-table-column>
        </el-table>
        <div class="flex justify-end mt-3" v-if="total > 0">
          <el-pagination
            background
            layout="total, sizes, prev, pager, next, jumper"
            :total="total"
            :current-page="currentPage"
            :page-size="pageSize"
            :page-sizes="[10, 20, 50, 100]"
            @current-change="handlePageChange"
            @size-change="handleSizeChange"
          />
        </div>
      </el-card>
    </div>

    <!-- 告警规则抽屉（加宽：80% / 最大 1080px） -->
    <el-drawer
      v-model="ruleDrawerVisible"
      title="告警规则"
      direction="rtl"
      size="80%"
      class="rule-drawer"
    >
      <div v-loading="ruleLoading">
        <el-empty v-if="!rules.length" description="暂无规则" :image-size="60" />
        <el-tabs v-else v-model="activeCategoryTab" class="rule-tabs">
          <el-tab-pane
            v-for="cat in RULE_CATEGORY_ORDER"
            :key="cat"
            :name="cat"
            :label="`${RULE_CATEGORY_LABEL[cat]} ${(rulesByCategory[cat] || []).filter(r => r.enabled).length}/${(rulesByCategory[cat] || []).length}`"
          >
            <div class="rule-list">
              <el-card
                v-for="rule in (rulesByCategory[cat] || [])"
                :key="rule.id"
                shadow="never"
                :body-style="{ padding: '12px 14px' }"
                class="rule-card"
              >
                <div class="flex items-center justify-between mb-2">
                  <div class="flex items-center gap-2 flex-wrap">
                    <el-tag :type="severityTagType(rule.severity)" size="small">
                      {{ rule.severity === "critical" ? "严重" : "警告" }}
                    </el-tag>
                    <el-tag type="info" size="small" effect="plain">{{ RULE_TYPE_LABEL[rule.rule_type] }}</el-tag>
                    <span class="font-medium">{{ rule.name }}</span>
                  </div>
                  <el-switch
                    v-model="rule.enabled"
                    @change="saveRule(rule)"
                  />
                </div>
                <el-form label-width="100px" size="small" class="rule-form">
                  <el-form-item v-if="!RULE_TYPES_NO_THRESHOLD.includes(rule.rule_type)" label="阈值">
                    <div class="flex items-center gap-2 w-full">
                      <el-input-number
                        v-model="rule.threshold"
                        :min="0"
                        style="flex: 1"
                      />
                      <span class="text-xs text-gray-400">{{ RULE_TYPE_UNIT[rule.rule_type] }}</span>
                      <span
                        v-if="RULE_TYPES_INVERTED.includes(rule.rule_type)"
                        class="text-xs text-orange-500"
                      >低于触发</span>
                    </div>
                  </el-form-item>
                  <el-form-item label="规则名称">
                    <el-input v-model="rule.name" />
                  </el-form-item>
                  <el-form-item label="严重级别">
                    <el-select v-model="rule.severity" style="width: 100%">
                      <el-option label="严重 (critical)" value="critical" />
                      <el-option label="警告 (warning)" value="warning" />
                    </el-select>
                  </el-form-item>
                  <el-form-item label="描述">
                    <el-input v-model="rule.description" type="textarea" :rows="2" />
                  </el-form-item>
                  <el-form-item>
                    <el-button type="primary" size="small" @click="saveRule(rule)">保存</el-button>
                  </el-form-item>
                </el-form>
              </el-card>
            </div>
          </el-tab-pane>
        </el-tabs>
      </div>
    </el-drawer>

    <!-- 告警渠道抽屉（与规则抽屉并列，加宽：80% / 最大 1080px） -->
    <el-drawer
      v-model="channelDrawerVisible"
      title="告警渠道"
      direction="rtl"
      size="80%"
      class="channel-drawer"
    >
      <div v-loading="channelLoading">
        <div class="mb-3">
          <el-button type="primary" size="small" @click="showCreateChannelForm = !showCreateChannelForm">
            {{ showCreateChannelForm ? "收起新建" : "新增渠道" }}
          </el-button>
        </div>

        <el-card v-if="showCreateChannelForm" shadow="never" :body-style="{ padding: '12px 14px' }" class="mb-3">
          <el-form label-width="100px" size="small">
            <el-form-item label="渠道名称">
              <el-input v-model="newChannel.name" placeholder="如：SRE 飞书群" />
            </el-form-item>
            <el-form-item label="渠道类型">
              <el-select
                v-model="newChannel.channel_type"
                style="width: 100%"
                @change="onChannelTypeChange(newChannel)"
              >
                <el-option
                  v-for="opt in channelTypeOptions"
                  :key="opt.value"
                  :label="opt.label"
                  :value="opt.value"
                />
              </el-select>
            </el-form-item>
            <el-form-item v-if="newChannel.channel_type !== 'email'" label="Webhook URL">
              <el-input
                v-model="newChannel.config.webhook_url"
                placeholder="https://open.feishu.cn/open-apis/bot/v2/hook/..."
              />
            </el-form-item>
            <el-form-item v-else label="收件人">
              <el-select
                v-model="newChannel.config.to"
                multiple
                filterable
                allow-create
                default-first-option
                placeholder="输入邮箱地址回车添加"
                style="width: 100%"
              />
            </el-form-item>
            <el-form-item label="订阅规则">
              <el-select
                v-model="newChannel.subscribed_rule_ids"
                multiple
                filterable
                collapse-tags
                collapse-tags-tooltip
                style="width: 100%"
                placeholder="选择该渠道接收哪些规则的告警"
                @change="(ids: string[]) => onSubscribedRuleIdsChange(ids, newChannel)"
              >
                <el-option
                  :key="ALL_RULES_ID"
                  label="全部规则"
                  :value="ALL_RULES_ID"
                />
                <el-option-group
                  v-for="grp in ruleSelectGroups"
                  :key="grp.category"
                  :label="grp.label"
                >
                  <el-option
                    v-for="opt in grp.options"
                    :key="opt.id"
                    :label="opt.name"
                    :value="opt.id"
                  />
                </el-option-group>
              </el-select>
            </el-form-item>
            <el-form-item label="启用">
              <el-switch v-model="newChannel.enabled" />
            </el-form-item>
            <el-form-item>
              <el-button type="primary" @click="createChannel">创建</el-button>
              <el-button @click="showCreateChannelForm = false">取消</el-button>
            </el-form-item>
          </el-form>
        </el-card>

        <el-empty v-if="!channels.length" description="暂无渠道" :image-size="60" />
        <div v-else class="channel-list">
          <el-card
            v-for="ch in channels"
            :key="ch.id"
            shadow="never"
            :body-style="{ padding: '12px 14px' }"
            class="rule-card"
          >
            <div class="flex items-center justify-between mb-2">
              <div class="flex items-center gap-2 flex-wrap">
                <el-tag type="info" size="small">{{ channelTypeLabel[ch.channel_type] }}</el-tag>
                <el-tag
                  v-if="ch.subscribed_all"
                  type="success"
                  size="small"
                >全部规则</el-tag>
                <span class="font-medium">{{ ch.name }}</span>
              </div>
              <el-switch
                v-model="ch.enabled"
                @change="saveChannel(ch)"
              />
            </div>
            <el-form label-width="100px" size="small" class="rule-form">
              <el-form-item label="渠道名称">
                <el-input v-model="ch.name" />
              </el-form-item>
              <el-form-item label="Webhook URL" v-if="ch.channel_type !== 'email'">
                <el-input v-model="ch.config.webhook_url" />
              </el-form-item>
              <el-form-item label="收件人" v-else>
                <el-select
                  v-model="ch.config.to"
                  multiple
                  filterable
                  allow-create
                  default-first-option
                  placeholder="输入邮箱地址回车添加"
                  style="width: 100%"
                />
              </el-form-item>
              <el-form-item label="订阅规则">
                <el-select
                  v-model="ch.subscribed_rule_ids"
                  multiple
                  filterable
                  collapse-tags
                  collapse-tags-tooltip
                  style="width: 100%"
                  :placeholder="ch.subscribed_all ? '已订阅全部规则' : '选择规则'"
                  @change="(ids: string[]) => onSubscribedRuleIdsChange(ids, ch)"
                >
                  <el-option
                    :key="ALL_RULES_ID"
                    label="全部规则"
                    :value="ALL_RULES_ID"
                  />
                  <el-option-group
                    v-for="grp in ruleSelectGroups"
                    :key="grp.category"
                    :label="grp.label"
                  >
                    <el-option
                      v-for="opt in grp.options"
                      :key="opt.id"
                      :label="opt.name"
                      :value="opt.id"
                    />
                  </el-option-group>
                </el-select>
                <div class="text-xs text-gray-400 mt-1">
                  {{ ch.subscribed_all ? "已订阅全部规则" : `当前订阅：${subscribedRuleNames(ch)}` }}
                </div>
              </el-form-item>
              <el-form-item label="启用">
                <el-switch v-model="ch.enabled" />
              </el-form-item>
              <el-form-item>
                <div class="flex justify-between w-full">
                  <el-button type="primary" size="small" @click="saveChannel(ch)">保存</el-button>
                  <el-button type="danger" size="small" plain @click="removeChannel(ch)">删除</el-button>
                </div>
              </el-form-item>
            </el-form>
          </el-card>
        </div>
      </div>
    </el-drawer>
  </div>
</template>

<style scoped>
.main { padding: 20px; }
.welcome { max-width: 1400px; margin: 0 auto; }
.rule-list, .channel-list { display: flex; flex-direction: column; gap: 12px; }
.rule-card { border: 1px solid #ebeef5; }
.rule-form :deep(.el-form-item) { margin-bottom: 10px; }
.rule-tabs :deep(.el-tabs__header) { margin-bottom: 12px; }
</style>

<style>
.rule-drawer .el-drawer { max-width: 1080px; }
.channel-drawer .el-drawer { max-width: 1080px; }
</style>
