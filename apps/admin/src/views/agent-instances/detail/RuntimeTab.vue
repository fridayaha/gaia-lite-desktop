<script setup lang="tsx">
import { ref, computed, onMounted, nextTick } from "vue";
import { useI18n } from "vue-i18n";
import type {
  AgentInstanceResponse,
  InstancePod,
  PodLogProfile
} from "@/api/manager/agentInstances";
import {
  getInstancePodsApi,
  getInstancePodLogsApi,
  getInstancePodLogSourcesApi
} from "@/api/manager/agentInstances";
import FeaturePlaceholder from "@/components/FeaturePlaceholder/index.vue";
import { message } from "@/utils/message";

defineOptions({ name: "InstanceRuntimeTab" });

const props = defineProps<{
  instanceId: string;
  instance?: AgentInstanceResponse | null;
}>();

const { t } = useI18n();

const isExternalDify = computed(
  () =>
    props.instance?.engine_type === "DIFY" &&
    props.instance?.dify_config?.external === true
);

const loading = ref(false);
const pods = ref<InstancePod[]>([]);
const loadError = ref(false);

// 日志抽屉
const logsVisible = ref(false);
const logsLoading = ref(false);
const logsContent = ref("");
const logsPodName = ref("");
const logSource = ref<"engine" | "gateway">("gateway");
const logProfile = ref<string>("");
const logProfileOptions = ref<PodLogProfile[]>([]);
const logTailLines = ref<number>(500);
const logAutoRefresh = ref(true);
const logContainerRef = ref<HTMLElement | null>(null);
let logRefreshTimer: ReturnType<typeof setInterval> | null = null;

const tailLineOptions = [200, 500, 1000, 2000];

// Profile 标签：真实姓名(用户名)，无用户信息时回退 profile_name
function profileLabel(p: PodLogProfile): string {
  if (p.real_name && p.username) return `${p.real_name}(${p.username})`;
  return p.username || p.real_name || p.profile_name;
}

const statusSummary = computed(() => {
  const running = pods.value.filter(i => i.status === "Running").length;
  const stopped = pods.value.filter(
    i => i.status === "Terminating" || i.status === "Pending"
  ).length;
  const abnormal = pods.value.filter(i => i.status === "CrashLoopBackOff").length;
  return { running, stopped, abnormal };
});

/** 状态颜色映射 */
const statusColors: Record<string, string> = {
  Running: "#00a870",
  Pending: "#f59e0b",
  CrashLoopBackOff: "#f56c6c",
  Terminating: "#909399"
};

/** 表格列定义 */
const columns = computed<TableColumnList>(() => [
  { label: t("agent.instance.col.name"), prop: "name", minWidth: 280 },
  {
    label: t("agent.instance.col.status"),
    prop: "status",
    width: 130,
    cellRenderer: ({ row }: any) => (
      <el-tag
        color={(statusColors[row.status] || "#909399") + "18"}
        style={{
          color: statusColors[row.status] || "#909399",
          border: `1px solid ${(statusColors[row.status] || "#909399")}40`
        }}
        effect="plain"
        size="small"
      >
        <span
          style={{
            display: "inline-block",
            width: "6px",
            height: "6px",
            borderRadius: "50%",
            background: statusColors[row.status] || "#909399",
            marginRight: "4px",
            verticalAlign: "middle"
          }}
        />
        {row.status}
      </el-tag>
    )
  },
  { label: t("agent.instance.col.node"), prop: "node", width: 140 },
  { label: "CPU", prop: "cpu", width: 90 },
  { label: t("agent.instance.col.memory"), prop: "memory", width: 100 },
  { label: t("agent.instance.col.restarts"), prop: "restarts", width: 100 },
  { label: t("agent.instance.col.age"), prop: "age", width: 90 },
  {
    label: t("agent.instance.col.operation"),
    width: 120,
    cellRenderer: ({ row }: any) => (
      <el-button type="primary" link size="small" onClick={() => handleViewLogs(row)}>
        {t("common.action.log")}
      </el-button>
    )
  }
]);

async function fetchLogSources() {
  if (!logsPodName.value) {
    logProfileOptions.value = [];
    return;
  }
  try {
    const res = await getInstancePodLogSourcesApi(props.instanceId, logsPodName.value);
    logProfileOptions.value = res.profiles || [];
    if (logProfileOptions.value.length > 0) {
      const names = logProfileOptions.value.map(p => p.profile_name);
      if (!logProfile.value || !names.includes(logProfile.value)) {
        logProfile.value = logProfileOptions.value[0].profile_name;
      }
      logSource.value = "gateway";
    } else {
      logSource.value = "engine";
      logProfile.value = "";
    }
  } catch {
    logProfileOptions.value = [];
    logSource.value = "engine";
  }
}

async function fetchLogs() {
  if (!logsPodName.value) return;
  logsLoading.value = true;
  try {
    const res = await getInstancePodLogsApi(props.instanceId, logsPodName.value, {
      tailLines: logTailLines.value,
      source: logSource.value,
      profile: logSource.value === "gateway" ? logProfile.value : undefined
    });
    logsContent.value = res.logs || "";
    await nextTick();
    if (logContainerRef.value) {
      logContainerRef.value.scrollTop = logContainerRef.value.scrollHeight;
    }
  } catch (e: any) {
    logsContent.value = e?.response?.data?.detail || t("agent.instance.msg.logFailed");
    message(t("agent.instance.msg.logFailed"), { type: "error" });
  } finally {
    logsLoading.value = false;
  }
}

function stopLogTimer() {
  if (logRefreshTimer) {
    clearInterval(logRefreshTimer);
    logRefreshTimer = null;
  }
}

function startLogTimer() {
  stopLogTimer();
  if (logAutoRefresh.value) {
    logRefreshTimer = setInterval(fetchLogs, 5000);
  }
}

async function handleViewLogs(pod: InstancePod) {
  logsPodName.value = pod.name;
  logsVisible.value = true;
  logsContent.value = "";
  logProfile.value = "";
  await fetchLogSources();
  await fetchLogs();
  startLogTimer();
}

function onLogSourceChange() {
  fetchLogs();
  startLogTimer();
}

function onLogClose() {
  stopLogTimer();
}

async function fetchData() {
  loading.value = true;
  loadError.value = false;
  try {
    const res = await getInstancePodsApi(props.instanceId);
    pods.value = res.items || [];
  } catch {
    loadError.value = true;
    pods.value = [];
  } finally {
    loading.value = false;
  }
}

defineExpose({ fetchData });
onMounted(fetchData);
</script>

<template>
  <div class="runtime-tab">
    <!-- 外部 Dify 实例：无 Pod，显示空状态 -->
    <FeaturePlaceholder
      v-if="isExternalDify"
      :title="t('agent.instance.runtime.noPodTitle')"
      :description="t('agent.instance.runtime.noPodDesc')"
    />
    <template v-else>
      <!-- 状态摘要 -->
      <el-row :gutter="16" class="mb-4">
        <el-col :span="8">
          <el-card shadow="never" class="summary-card">
            <div class="summary-body">
              <span class="summary-dot" style="background: #00a870" />
              <span class="summary-label">{{ t("agent.instance.summary.running") }}</span>
              <span class="summary-value">{{ statusSummary.running }}</span>
            </div>
          </el-card>
        </el-col>
        <el-col :span="8">
          <el-card shadow="never" class="summary-card">
            <div class="summary-body">
              <span class="summary-dot" style="background: #909399" />
              <span class="summary-label">{{ t("agent.instance.summary.stopped") }}</span>
              <span class="summary-value">{{ statusSummary.stopped }}</span>
            </div>
          </el-card>
        </el-col>
        <el-col :span="8">
          <el-card shadow="never" class="summary-card">
            <div class="summary-body">
              <span class="summary-dot" style="background: #f56c6c" />
              <span class="summary-label">{{ t("agent.instance.summary.failed") }}</span>
              <span class="summary-value">{{ statusSummary.abnormal }}</span>
            </div>
          </el-card>
        </el-col>
      </el-row>

      <!-- Pod 表格 -->
      <pure-table
        v-if="!loadError"
        :data="pods"
      :columns="columns"
      :loading="loading"
      stripe
      border
      adaptive
      header-cell-class-name="table-header"
    />
    <el-empty v-else :description="t('agent.instance.msg.loadFailed')">
      <el-button type="primary" @click="fetchData">{{ t("common.action.retry") }}</el-button>
    </el-empty>

    <!-- 日志抽屉 -->
    <el-drawer
      v-model="logsVisible"
      :title="logsPodName"
      size="60%"
      @close="onLogClose"
    >
      <div class="log-controls">
        <span class="text-sm text-gray-500">{{ t("engine.log.source") }}</span>
        <el-select v-model="logSource" style="width: 140px" @change="onLogSourceChange">
          <el-option :label="t('engine.log.sourceEngine')" value="engine" />
          <el-option
            :label="t('engine.log.sourceGateway')"
            value="gateway"
            :disabled="logProfileOptions.length === 0"
          />
        </el-select>
        <el-select
          v-if="logSource === 'gateway'"
          v-model="logProfile"
          style="width: 220px"
          :placeholder="t('engine.log.selectProfile')"
          @change="onLogSourceChange"
        >
          <el-option
            v-for="p in logProfileOptions"
            :key="p.profile_name"
            :label="profileLabel(p)"
            :value="p.profile_name"
          />
        </el-select>
        <span class="text-sm text-gray-500">{{ t("engine.log.tailLines") }}</span>
        <el-select v-model="logTailLines" style="width: 100px" @change="fetchLogs">
          <el-option
            v-for="n in tailLineOptions"
            :key="n"
            :label="String(n)"
            :value="n"
          />
        </el-select>
        <el-switch v-model="logAutoRefresh" :active-text="t('engine.log.autoRefresh')" @change="startLogTimer" />
        <el-button :loading="logsLoading" size="small" @click="fetchLogs">
          {{ t("engine.log.refresh") }}
        </el-button>
      </div>
      <div v-loading="logsLoading" class="log-body">
        <pre ref="logContainerRef" class="pod-logs">{{ logsContent || t("agent.instance.msg.logEmpty") }}</pre>
      </div>
    </el-drawer>
    </template>
  </div>
</template>

<style scoped>
.runtime-tab {
  margin-bottom: 20px;
}

.summary-card {
  border-radius: 8px;
}

.summary-body {
  display: flex;
  align-items: center;
  gap: 10px;
}

.summary-dot {
  width: 10px;
  height: 10px;
  border-radius: 50%;
  display: inline-block;
}

.summary-label {
  font-size: 14px;
  color: var(--el-text-color-secondary);
}

.summary-value {
  font-size: 18px;
  font-weight: 600;
  margin-left: auto;
}

:deep(.table-header) {
  font-weight: 600;
  color: var(--el-text-color-primary);
}

.pod-logs {
  margin: 0;
  padding: 12px;
  font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
  font-size: 12px;
  line-height: 1.6;
  background: var(--el-fill-color-light);
  border-radius: 6px;
  max-height: calc(100vh - 220px);
  overflow: auto;
  white-space: pre-wrap;
  word-break: break-all;
}

.log-controls {
  display: flex;
  align-items: center;
  gap: 10px;
  flex-wrap: wrap;
  margin-bottom: 12px;
}

.log-body {
  min-height: 200px;
}
</style>
