<script setup lang="tsx">
import { ref, computed, onMounted } from "vue";
import { useI18n } from "vue-i18n";
import { message } from "@/utils/message";
import {
  getPoolPodsApi,
  type PoolPod,
} from "@/api/manager/resourcePools";

defineOptions({ name: "ResourcePoolPodsTab" });

const props = defineProps<{
  poolId: string;
}>();

const emit = defineEmits<{
  viewLogs: [podName: string];
}>();

const { t } = useI18n();
const loading = ref(false);
const pods = ref<PoolPod[]>([]);

const statusSummary = computed(() => {
  const running = pods.value.filter(i => i.status === "Running").length;
  const stopped = pods.value.filter(i => i.status === "Terminating" || i.status === "Pending").length;
  const abnormal = pods.value.filter(i => i.status === "CrashLoopBackOff" || i.status === "Error").length;
  return { running, stopped, abnormal };
});

const statusColors: Record<string, string> = {
  Running: "#00a870",
  Pending: "#f59e0b",
  CrashLoopBackOff: "#f56c6c",
  Terminating: "#909399",
  Error: "#f56c6c",
};

// 同智能体的 Pod 排在一起，供 spanMethod 合并「智能体」列单元格（一对多展示）
const sortedPods = computed(() =>
  [...pods.value].sort(
    (a, b) =>
      (a.agent_name || "").localeCompare(b.agent_name || "") ||
      (a.name || "").localeCompare(b.name || "")
  )
);

// agentSpans[i]：第 i 行「智能体」列的 rowspan；0 表示被合并（不渲染）
const agentSpans = computed(() => {
  const spans: number[] = [];
  const rows = sortedPods.value;
  let i = 0;
  while (i < rows.length) {
    const cur = rows[i].agent_name || "";
    if (!cur) {
      spans[i] = 1;
      i += 1;
      continue;
    }
    let j = i + 1;
    while (j < rows.length && (rows[j].agent_name || "") === cur) j += 1;
    spans[i] = j - i;
    for (let k = i + 1; k < j; k++) spans[k] = 0;
    i = j;
  }
  return spans;
});

function spanMethod({ columnIndex, rowIndex }: { row: PoolPod; column: any; rowIndex: number; columnIndex: number }) {
  if (columnIndex === 0) {
    const s = agentSpans.value[rowIndex];
    return s === 0 ? { rowspan: 0, colspan: 0 } : { rowspan: s, colspan: 1 };
  }
  return { rowspan: 1, colspan: 1 };
}

const columns = computed<TableColumnList>(() => [
  {
    label: t("engine.pod.col.agent"),
    prop: "agent_name",
    minWidth: 160,
    cellRenderer: ({ row }: any) => (
      <span class="text-sm">{row.agent_name || "—"}</span>
    )
  },
  { label: t("engine.pod.col.name"), prop: "name", minWidth: 280 },
  {
    label: t("engine.pod.col.status"),
    prop: "status",
    width: 130,
    cellRenderer: ({ row }: any) => (
      <el-tag
        color={(statusColors[row.status] || "#909399") + "18"}
        style={{ color: statusColors[row.status] || "#909399", border: `1px solid ${(statusColors[row.status] || "#909399")}40` }}
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
  { label: t("engine.pod.col.node"), prop: "node", width: 140 },
  { label: "CPU", prop: "cpu", width: 90 },
  { label: t("engine.pod.col.memory"), prop: "memory", width: 100 },
  { label: t("engine.pod.col.restarts"), prop: "restarts", width: 100 },
  { label: t("engine.pod.col.age"), prop: "age", width: 90 },
  {
    label: t("engine.pod.col.operation"),
    width: 120,
    cellRenderer: ({ row }: any) => (
      <div style="display:flex;gap:4px;">
        <el-button type="primary" link size="small" onClick={() => handleViewLogs(row)}>
          {t("engine.pod.action.log")}
        </el-button>
      </div>
    )
  }
]);

function handleViewLogs(pod: PoolPod) {
  emit("viewLogs", pod.name);
}

async function fetchPods() {
  loading.value = true;
  try {
    const res = await getPoolPodsApi(props.poolId);
    pods.value = res.items;
  } catch (e: any) {
    message(t("engine.pod.msg.fetchFailed", { detail: e?.message || e }), { type: "error" });
  } finally {
    loading.value = false;
  }
}

defineExpose({ fetchPods });
onMounted(fetchPods);
</script>

<template>
  <div class="pods-tab">
    <!-- 状态摘要 -->
    <el-row :gutter="16" class="mb-4">
      <el-col :span="8">
        <el-card shadow="never" class="summary-card">
          <div class="summary-body">
            <span class="summary-dot" style="background: #00a870" />
            <span class="summary-label">{{ t("common.status.running") }}</span>
            <span class="summary-value">{{ statusSummary.running }}</span>
          </div>
        </el-card>
      </el-col>
      <el-col :span="8">
        <el-card shadow="never" class="summary-card">
          <div class="summary-body">
            <span class="summary-dot" style="background: #909399" />
            <span class="summary-label">{{ t("common.status.stopped") }}</span>
            <span class="summary-value">{{ statusSummary.stopped }}</span>
          </div>
        </el-card>
      </el-col>
      <el-col :span="8">
        <el-card shadow="never" class="summary-card">
          <div class="summary-body">
            <span class="summary-dot" style="background: #f56c6c" />
            <span class="summary-label">{{ t("common.status.failed") }}</span>
            <span class="summary-value">{{ statusSummary.abnormal }}</span>
          </div>
        </el-card>
      </el-col>
    </el-row>

    <!-- Pod 表格 -->
    <pure-table
      :data="sortedPods"
      :columns="columns"
      :loading="loading"
      :span-method="spanMethod"
      stripe
      border
      adaptive
      header-cell-class-name="table-header"
    />
  </div>
</template>

<style scoped>
.pods-tab {
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
</style>
