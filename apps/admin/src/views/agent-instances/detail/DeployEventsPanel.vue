<script setup lang="ts">
/**
 * 部署进度面板 —— 纯展示组件，由父组件轮询 deployment-status 后传入 deployStatus，
 * 据 (status, pod_phase) 推导 4 档粗粒度进度。不再消费 SSE。
 *
 * 父组件负责轮询与终态判定（RUNNING/FAILED/超时），本面板仅渲染当前快照。
 */
import { computed } from "vue";
import { useI18n } from "vue-i18n";
import type { DeploymentStatus } from "@/api/manager/agentInstances";
import CheckLine from "~icons/ri/check-line";
import Loader4Line from "~icons/ri/loader-4-line";
import CloseCircleLine from "~icons/ri/close-circle-line";
import RefreshLine from "~icons/ri/refresh-line";

defineOptions({ name: "DeployEventsPanel" });

const props = defineProps<{
  deployStatus: DeploymentStatus | null;
}>();

const emit = defineEmits<{
  /** 用户点击重试（父组件重新触发 deploy） */
  retry: [];
}>();

const { t } = useI18n();

type StepStatus = "pending" | "running" | "done" | "failed";
type Step = { key: string; label: string; status: StepStatus };

/** 4 档步骤定义（与下方 progress/steps 推导保持同步） */
const stepDefs = computed(() => [
  { key: "starting", label: t("instance.deployStep.starting") },
  { key: "create_pod", label: t("instance.deployStep.createPod") },
  { key: "wait_running", label: t("instance.deployStep.waitRunning") },
  { key: "ready", label: t("instance.deployStep.ready") }
]);

const phase = computed<"streaming" | "done" | "failed">(() => {
  const s = props.deployStatus?.status;
  if (s === "RUNNING") return "done";
  if (s === "FAILED") return "failed";
  return "streaming";
});

/** pod 是否已 Running（k8s Pod phase） */
const podRunning = computed(() => props.deployStatus?.pod_phase === "Running");
const podPending = computed(() => props.deployStatus?.pod_phase === "Pending");
const hasPod = computed(() => !!props.deployStatus?.pod_name || !!props.deployStatus?.pod_phase);

/** 推导各步骤状态：DEPLOYING 下按 pod 出现/Running 逐级点亮；RUNNING 全 done */
const steps = computed<Step[]>(() => {
  const s = props.deployStatus?.status;
  const failed = phase.value === "failed";
  const running = phase.value === "streaming";
  const done = phase.value === "done";
  // 哪一步正在跑（running 态下）
  let activeIdx = 0;
  if (running) {
    if (podRunning.value) activeIdx = 2; // 等待引擎就绪
    else if (hasPod.value || podPending.value) activeIdx = 1; // 创建 Pod
    else activeIdx = 0; // 申请沙箱
  }
  return stepDefs.value.map((d, idx) => {
    let status: StepStatus = "pending";
    if (done) status = "done";
    else if (failed) status = idx < 3 ? "done" : "failed"; // 失败时已完成的保留 done，最后一步 failed
    else if (running) {
      if (idx < activeIdx) status = "done";
      else if (idx === activeIdx) status = "running";
      else status = "pending";
    }
    return { ...d, status };
  });
});

const progress = computed(() => {
  switch (phase.value) {
    case "done":
      return 100;
    case "failed":
      return 100;
    default: {
      if (podRunning.value) return 75;
      if (hasPod.value || podPending.value) return 35;
      return 15;
    }
  }
});

const progressStatus = computed(() =>
  phase.value === "failed" ? "exception" : phase.value === "done" ? "success" : undefined
);

const failReason = computed(() => props.deployStatus?.error_message || t("instance.deployStep.failedUnknown"));

function handleRetry() {
  emit("retry");
}
</script>

<template>
  <el-card shadow="never" class="deploy-events-panel">
    <template #header>
      <div class="panel-header">
        <span class="panel-title">
          <el-icon v-if="phase === 'streaming'" class="is-loading"><Loader4Line /></el-icon>
          <el-icon v-else-if="phase === 'done'" style="color: #00a870"><CheckLine /></el-icon>
          <el-icon v-else style="color: #f56c6c"><CloseCircleLine /></el-icon>
          {{ t("instance.deployStep.title") }}
        </span>
        <el-button
          v-if="phase === 'failed'"
          type="primary"
          plain
          size="small"
          @click="handleRetry"
        >
          <el-icon class="mr-1"><RefreshLine /></el-icon>
          {{ t("common.action.retry") }}
        </el-button>
      </div>
    </template>

    <el-progress
      :percentage="progress"
      :status="progressStatus"
      :stroke-width="10"
      class="mb-4"
    />

    <div class="step-list">
      <div v-for="s in steps" :key="s.key" class="step-item">
        <span class="step-icon">
          <el-icon v-if="s.status === 'done'" style="color: #00a870"><CheckLine /></el-icon>
          <el-icon v-else-if="s.status === 'running'" class="is-loading" style="color: #386bf5">
            <Loader4Line />
          </el-icon>
          <el-icon v-else-if="s.status === 'failed'" style="color: #f56c6c"><CloseCircleLine /></el-icon>
          <el-icon v-else style="color: #909399"><RefreshLine /></el-icon>
        </span>
        <div class="step-body">
          <div class="step-label">{{ s.label }}</div>
        </div>
      </div>
    </div>

    <div v-if="phase === 'failed' && failReason" class="fail-reason">
      {{ t("instance.deployStep.failedReason", { reason: failReason }) }}
    </div>
  </el-card>
</template>

<style scoped>
.deploy-events-panel {
  border-radius: 8px;
  margin-bottom: 16px;
}

.panel-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
}

.panel-title {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  font-weight: 600;
  font-size: 14px;
}

.step-list {
  display: flex;
  flex-direction: column;
  gap: 12px;
  margin-bottom: 12px;
}

.step-item {
  display: flex;
  align-items: flex-start;
  gap: 10px;
}

.step-icon {
  font-size: 18px;
  line-height: 1.2;
  flex-shrink: 0;
}

.step-body {
  flex: 1;
  min-width: 0;
}

.step-label {
  font-size: 13px;
  font-weight: 500;
  color: var(--el-text-color-primary);
}

.fail-reason {
  margin-top: 10px;
  padding: 8px 12px;
  background: var(--el-color-danger-light-9);
  color: var(--el-color-danger);
  border-radius: 6px;
  font-size: 12px;
  word-break: break-all;
}
</style>
