<script setup lang="ts">
import { ref, computed, onMounted, onUnmounted, h, provide } from "vue";
import { useRoute, useRouter } from "vue-router";
import { useI18n } from "vue-i18n";
import dayjs from "dayjs";
import {
  getInstanceApi,
  publishInstanceApi,
  offlineInstanceApi,
  deleteInstanceApi,
  getDeploymentStatusApi,
  deployInstanceApi,
  suspendInstanceApi,
  resumeInstanceApi,
  restartInstanceApi,
  destroyInstanceApi,
  upgradeInstanceApi,
  type AgentInstanceResponse,
  type DeploymentStatus
} from "@/api/manager/agentInstances";
import { getVersionsApi } from "@/api/manager/agentDefinitions";
import { openInstanceDialog } from "../utils/hook";
import { message } from "@/utils/message";
import { ElMessageBox } from "element-plus";
import OverviewTab from "./OverviewTab.vue";
import RuntimeTab from "./RuntimeTab.vue";
import MonitorTab from "./MonitorTab.vue";
import MemoryTab from "./MemoryTab.vue";
import ChannelsTab from "./ChannelsTab.vue";
import ApiKeysTab from "./ApiKeysTab.vue";
import DeployEventsPanel from "./DeployEventsPanel.vue";
import HermesLogo from "../components/icons/HermesLogo.vue";
import OpenClawLogo from "../components/icons/OpenClawLogo.vue";
import DifyLogo from "../components/icons/DifyLogo.vue";
import { IconifyIconOffline } from "@/components/ReIcon";
import More2Fill from "~icons/ri/more-2-fill";
import RefreshLine from "~icons/ri/refresh-line";
import EditLine from "~icons/ri/edit-line";
import PlayLine from "~icons/ri/play-line";
import PauseLine from "~icons/ri/pause-line";
import DeleteBinLine from "~icons/ri/delete-bin-line";
import RocketLine from "~icons/ri/rocket-line";
import Notification3Line from "~icons/ri/notification-3-line";
import PlayCircleLine from "~icons/ri/play-circle-line";
import PauseCircleLine from "~icons/ri/pause-circle-line";
import ErrorWarningLine from "~icons/ri/error-warning-line";
import TimeLine from "~icons/ri/time-line";
import ArchiveLine from "~icons/ri/archive-line";
import Loader4Line from "~icons/ri/loader-4-line";
import FileCopyLine from "~icons/ri/file-copy-line";
import { copyTextToClipboard } from "@pureadmin/utils";

defineOptions({ name: "AgentInstanceDetail" });

const route = useRoute();
const router = useRouter();
const { t } = useI18n();

const instanceId = computed(() => route.params.id as string);
const activeTab = ref("overview");
const loading = ref(true);
const instance = ref<AgentInstanceResponse | null>(null);
const deployStatus = ref<DeploymentStatus | null>(null);
const actionLoading = ref<string | null>(null);
let pollTimer: ReturnType<typeof setInterval> | null = null;

// ── 部署进度面板（deploy 用：POST 异步返回 DEPLOYING 后轮询 deployment-status）──
const deploying = ref(false);
const deployPanelActive = ref(false);
let deployPollTimer: ReturnType<typeof setInterval> | null = null;
let deployDeadline = 0;

/** 业务状态映射 */
const statusConfig = computed<Record<string, { label: string; color: string }>>(() => ({
  DRAFT: { label: t("common.status.draft"), color: "#f59e0b" },
  PUBLISHED: { label: t("instance.stats.published"), color: "#00a870" },
  OFFLINE: { label: t("common.status.offline"), color: "#909399" }
}));

/** 部署状态映射 */
const deployStatusConfig = computed<
  Record<string, { label: string; color: string; icon: any; spin?: boolean }>
>(() => ({
  RUNNING: { label: t("common.status.running"), color: "#00a870", icon: PlayCircleLine },
  SUSPENDED: { label: t("common.status.suspended"), color: "#f59e0b", icon: PauseCircleLine },
  FAILED: { label: t("common.status.failed"), color: "#f56c6c", icon: ErrorWarningLine },
  PENDING: { label: t("common.status.pending"), color: "#909399", icon: TimeLine },
  DEPLOYING: { label: t("common.status.deploying"), color: "#386bf5", icon: Loader4Line, spin: true },
  ARCHIVED: { label: t("common.status.archived"), color: "#909399", icon: ArchiveLine }
}));

/** 访问范围标签映射 */
const scopeLabelMap = computed<Record<string, string>>(() => ({
  ALL: t("common.scope.allFull"),
  USER: t("common.scope.user"),
  USER_GROUP: t("common.scope.userGroup")
}));

const engineColors: Record<string, string> = {
  HERMES: "#386bf5",
  OPENCLAW: "#e6a23c"
};

const avatarBg = computed(() => {
  if (!instance.value?.engine_type) return "#909399";
  return engineColors[instance.value.engine_type] || "#909399";
});

// 浏览器沙箱开关态（仅 Hermes 引擎实例有意义：config.yaml.tmpl 的 browser toolset 仅 Hermes）
const isHermes = computed(() => instance.value?.engine_type === "HERMES");
const browserSandboxEnabled = computed(
  () => !!(instance.value as any)?.runtime_config?.browser_sandbox?.enabled
);

// ── 状态判断逻辑 ──
/** Manager 层面状态 */
const canPublish = computed(
  () => instance.value?.status === "DRAFT" || instance.value?.status === "OFFLINE"
);
const canOffline = computed(() => instance.value?.status === "PUBLISHED");

/** Dify 外接模式：无 Pod，部署/暂停/恢复/重启/销毁都无意义（engine_url 直接指向外部 Dify）。
 *  只允许上线/下线（业务可见性）+ 删除。 */
const isDifyExternal = computed(() => {
  const cfg = (instance.value as any)?.dify_config || {};
  return instance.value?.engine_type === "DIFY" && !!cfg.base_url;
});

/** Controller 部署层面状态 */
const isDeployed = computed(() => deployStatus.value?.status === "RUNNING");
const isSuspended = computed(
  () =>
    deployStatus.value?.status === "SUSPENDED" || deployStatus.value?.status === "ARCHIVED"
);
const isFailed = computed(() => deployStatus.value?.status === "FAILED");
/** 部署/重新部署：未部署 或 SUSPENDED/FAILED/PENDING（Dify 外接永远不显示） */
const canDeploy = computed(
  () =>
    !isDifyExternal.value &&
    (!deployStatus.value ||
      isSuspended.value ||
      isFailed.value ||
      deployStatus.value?.status === "PENDING")
);
const canSuspend = computed(
  () => !isDifyExternal.value && (isDeployed.value || isFailed.value)
);
/** 恢复：SUSPENDED 时（显式按钮） */
const canResume = computed(
  () => !isDifyExternal.value && deployStatus.value?.status === "SUSPENDED"
);
/** 重启：RUNNING 时（Dify 外接永远不显示） */
const canRestart = computed(
  () => !isDifyExternal.value && isDeployed.value
);
const canDestroy = computed(
  () =>
    !isDifyExternal.value &&
    !!deployStatus.value &&
    deployStatus.value.status !== "ARCHIVED"
);

async function fetchInstance() {
  loading.value = true;
  try {
    instance.value = await getInstanceApi(instanceId.value);
    fetchDeployStatus();
  } catch {
    message(t("instance.msg.loadFailed"), { type: "error" });
    router.back();
  } finally {
    loading.value = false;
  }
}

// ── 部署状态轮询 ──
async function fetchDeployStatus() {
  try {
    deployStatus.value = await getDeploymentStatusApi(instanceId.value);
  } catch {
    deployStatus.value = null;
  }
}

function startPolling() {
  fetchDeployStatus();
  pollTimer = setInterval(fetchDeployStatus, 15000); // 每 15 秒
}

onMounted(() => {
  fetchInstance().then(() => startPolling());
});

onUnmounted(() => {
  if (pollTimer) clearInterval(pollTimer);
  if (restartPollTimer) clearInterval(restartPollTimer);
  if (deployPollTimer) clearInterval(deployPollTimer);
});

// ── 引擎重启共享态（部署/重启 共用） ──
const restarting = ref(false);
/** 部署中或重启中：禁用动作按钮 */
const busy = computed(() => restarting.value || deploying.value);
let restartPollTimer: ReturnType<typeof setInterval> | null = null;
let restartBaselinePod: string | null = null;
let restartBaselineStart: string | null = null;
let restartDeadline = 0;

async function startRestart() {
  if (restarting.value) return;
  // 记录重启前 Pod 基线（rollout 建新 Pod，名字/启动时间会变）
  restartBaselinePod = deployStatus.value?.pod_name ?? null;
  restartBaselineStart = deployStatus.value?.pod_start_time ?? null;
  restarting.value = true;
  message(t("instance.detail.restartSubmitted"), { type: "info" });
  restartDeadline = Date.now() + 120_000;
  await fetchDeployStatus();
  if (restartPollTimer) clearInterval(restartPollTimer);
  restartPollTimer = setInterval(pollRestart, 4000);
}

async function pollRestart() {
  await fetchDeployStatus();
  const s = deployStatus.value;
  if (s?.status === "RUNNING") {
    const podChanged = s.pod_name && s.pod_name !== restartBaselinePod;
    const startChanged = s.pod_start_time && s.pod_start_time !== restartBaselineStart;
    if (podChanged || startChanged) {
      finishRestart(true);
      return;
    }
  }
  if (Date.now() > restartDeadline) {
    finishRestart(false);
  }
}

function finishRestart(ok: boolean) {
  if (restartPollTimer) {
    clearInterval(restartPollTimer);
    restartPollTimer = null;
  }
  restarting.value = false;
  if (ok) {
    message(t("instance.detail.restartReady"), { type: "success" });
  } else {
    message(t("instance.detail.restartTimeout"), { type: "warning" });
  }
}

// 提供给子 Tab 触发重启态
provide("engineRestart", { restarting, startRestart });

// ── Manager 操作 ──
async function handlePublish() {
  if (!instance.value) return;
  try {
    instance.value = await publishInstanceApi(instance.value.id);
    message(t("instance.msg.onlined"), { type: "success" });
  } catch (e: any) {
    message(
      e?.response?.data?.detail || t("instance.msg.onlinedFailed"),
      { type: "error" }
    );
  }
}

async function handleOffline() {
  if (!instance.value) return;
  try {
    instance.value = await offlineInstanceApi(instance.value.id);
    message(t("instance.msg.offlined"), { type: "success" });
  } catch (e: any) {
    message(
      e?.response?.data?.detail || t("instance.msg.offlineFailed"),
      { type: "error" }
    );
  }
}

async function handleDelete() {
  if (!instance.value) return;
  try {
    await deleteInstanceApi(instance.value.id);
    message(t("common.msg.deleteSuccess"), { type: "success" });
    router.push("/agent-instances/index");
  } catch (e: any) {
    message(
      t("instance.msg.deleteFailedDetail", {
        detail: e?.response?.data?.detail || e?.message || e
      }),
      { type: "error" }
    );
  }
}

// ── 版本增量热更新（不重建 Pod）──
async function handleUpgrade() {
  if (!instance.value || actionLoading.value) return;
  const inst = instance.value;
  const targetId = inst.definition_current_version_id;
  if (!targetId) return;
  actionLoading.value = "upgrade";
  try {
    // 取目标版本元数据展示 change_log
    let versionNo = "";
    let changeLog = "";
    try {
      const versions = await getVersionsApi(inst.definition_id);
      const v = (versions || []).find(x => x.id === targetId);
      if (v) {
        versionNo = v.version_no;
        changeLog = v.change_log || "";
      }
    } catch {
      /* 取不到版本说明不阻断 */
    }
    const detailLines = [
      `${t("instance.version.targetVersion")}: ${versionNo || "—"}`,
      `${t("instance.version.changeLog")}: ${changeLog || t("instance.version.noChangeLog")}`,
      "",
      t("instance.version.confirm")
    ].join("\n");
    await ElMessageBox.confirm(detailLines, t("instance.version.upgrade"), {
      confirmButtonText: t("instance.version.upgrade"),
      cancelButtonText: t("common.action.cancel"),
      type: "warning"
    });
    const res = await upgradeInstanceApi(inst.id, targetId);
    if (!res.applied) {
      message(t("instance.version.upgradeNotRunning"), { type: "info" });
    } else if (res.restarted) {
      message(t("instance.version.upgradeRestart"), { type: "warning" });
    } else {
      message(t("instance.version.upgradeHot"), { type: "success" });
    }
    await fetchInstance();
  } catch (e: any) {
    if (e === "cancel" || e?.message === "cancel") return;
    message(
      t("instance.version.upgradeFailed", {
        detail: e?.response?.data?.detail || e?.message || e
      }),
      { type: "error" }
    );
  } finally {
    actionLoading.value = null;
  }
}

async function handleDeploy() {
  if (!instance.value || actionLoading.value) return;
  // 已运行时确认是否重新部署（强制重建 Pod）
  if (isDeployed.value) {
    try {
      await ElMessageBox.confirm(
        t("instance.detail.confirmRedeploy"),
        t("instance.detail.redeployTitle"),
        {
          confirmButtonText: t("instance.deploy"),
          cancelButtonText: t("common.action.cancel"),
          type: "warning"
        }
      );
    } catch {
      return;
    }
  }
  actionLoading.value = "deploy";
  deploying.value = true;
  deployPanelActive.value = true;
  try {
    // deploy POST 异步：立即置 DEPLOYING 返回，主体在后台跑
    await deployInstanceApi(instance.value.id);
  } catch (e: any) {
    // POST 本身失败（404/409/网络）—— 关面板，不进入轮询
    deploying.value = false;
    deployPanelActive.value = false;
    message(
      t("instance.msg.deployFailed", {
        detail: e?.response?.data?.detail || e?.message || e
      }),
      { type: "error" }
    );
    return;
  } finally {
    actionLoading.value = null;
  }
  // 轮询 deployment-status 直至 RUNNING/FAILED/超时
  await fetchDeployStatus();
  deployDeadline = Date.now() + 180_000; // 部署最多等 3min
  if (deployPollTimer) clearInterval(deployPollTimer);
  deployPollTimer = setInterval(pollDeploy, 2000);
}

async function pollDeploy() {
  await fetchDeployStatus();
  const s = deployStatus.value;
  if (s?.status === "RUNNING") {
    finishDeploy(true);
    return;
  }
  if (s?.status === "FAILED") {
    finishDeploy(false, s.error_message || undefined);
    return;
  }
  if (Date.now() > deployDeadline) {
    finishDeploy(false, t("instance.deployStep.timeout"));
  }
}

function finishDeploy(ok: boolean, reason?: string) {
  if (deployPollTimer) {
    clearInterval(deployPollTimer);
    deployPollTimer = null;
  }
  deploying.value = false;
  deployPanelActive.value = !ok; // 成功关面板；失败保留面板展示错误 + 重试
  fetchInstance();
  if (ok) {
    message(t("instance.detail.deployReady"), { type: "success" });
  } else {
    message(t("instance.deployStep.deployFailed", { reason: reason || "" }), { type: "error" });
  }
}

function onDeployRetry() {
  deployPanelActive.value = false;
  handleDeploy();
}

async function handleSuspend() {
  if (!instance.value || actionLoading.value) return;
  actionLoading.value = "suspend";
  try {
    await suspendInstanceApi(instance.value.id);
    message(t("instance.msg.suspended"), { type: "success" });
    await fetchDeployStatus();
  } catch (e: any) {
    message(
      t("instance.msg.suspendFailed", {
        detail: e?.response?.data?.detail || e?.message || e
      }),
      { type: "error" }
    );
  } finally {
    actionLoading.value = null;
  }
}

async function handleResume() {
  if (!instance.value || actionLoading.value) return;
  actionLoading.value = "resume";
  try {
    await resumeInstanceApi(instance.value.id);
    message(t("instance.msg.resumed"), { type: "success" });
    // resume_agent 只 scale 0→1；刷新状态即可（restart 轮询覆盖就绪检测）
    await fetchDeployStatus();
  } catch (e: any) {
    message(
      t("instance.msg.resumeFailed", {
        detail: e?.response?.data?.detail || e?.message || e
      }),
      { type: "error" }
    );
  } finally {
    actionLoading.value = null;
  }
}

async function handleRestart() {
  if (!instance.value || actionLoading.value) return;
  actionLoading.value = "restart";
  try {
    await restartInstanceApi(instance.value.id);
    message(t("instance.msg.restarted"), { type: "success" });
    await startRestart();
  } catch (e: any) {
    message(
      t("instance.msg.restartFailed", {
        detail: e?.response?.data?.detail || e?.message || e
      }),
      { type: "error" }
    );
  } finally {
    actionLoading.value = null;
  }
}

async function handleDestroy() {
  if (!instance.value || actionLoading.value) return;
  try {
    await ElMessageBox.confirm(
      t("instance.detail.confirmDestroy"),
      t("instance.detail.destroyTitle"),
      {
        confirmButtonText: t("instance.destroy"),
        cancelButtonText: t("common.action.cancel"),
        type: "warning"
      }
    );
  } catch {
    return;
  }
  actionLoading.value = "destroy";
  try {
    await destroyInstanceApi(instance.value.id);
    message(t("instance.msg.destroyed"), { type: "success" });
    deployStatus.value = null;
  } catch (e: any) {
    message(
      t("instance.msg.destroyFailed", {
        detail: e?.response?.data?.detail || e?.message || e
      }),
      { type: "error" }
    );
  } finally {
    actionLoading.value = null;
  }
}

function goToResourcePool() {
  if (instance.value?.resource_pool_id) {
    router.push(`/resource-pools/detail/${instance.value.resource_pool_id}`);
  }
}

function goToDefinition() {
  if (instance.value?.definition_id) {
    router.push(`/agent-definitions/detail/${instance.value.definition_id}`);
  }
}

function copyAgentId() {
  if (!instance.value?.id) return;
  const ok = copyTextToClipboard(instance.value.id);
  if (ok) {
    message("Agent ID 已复制", { type: "success" });
  } else {
    message("复制失败", { type: "error" });
  }
}

// ── 编辑 ──
function openEditDialog() {
  if (!instance.value) return;
  openInstanceDialog(
    "edit",
    {
      id: instance.value.id,
      name: instance.value.name,
      description: instance.value.description,
      definition_id: instance.value.definition_id,
      version_id: instance.value.version_id ?? "",
      resource_pool_id: instance.value.resource_pool_id,
      group_id: instance.value.group_id,
      dify_config: instance.value.dify_config,
      runtime_config: (instance.value as any).runtime_config
    },
    fetchInstance
  );
}

function onMenuCommand(cmd: string) {
  if (cmd === "publish") handlePublish();
  else if (cmd === "offline") handleOffline();
  else if (cmd === "delete") handleDelete();
}
</script>

<template>
  <div v-loading="loading" class="main">
    <div class="agent-detail">
    <!-- Header Card -->
    <el-card shadow="never" class="detail-header">
      <el-row :gutter="24" align="middle">
        <el-col :xs="24" :sm="16">
          <div class="header-main">
            <!-- Avatar -->
            <div
              class="header-avatar"
              :style="{ background: avatarBg + '18', color: avatarBg }"
            >
              <HermesLogo
                v-if="instance?.engine_type === 'HERMES'"
                class="avatar-icon"
              />
              <OpenClawLogo
                v-else-if="instance?.engine_type === 'OPENCLAW'"
                class="avatar-icon"
              />
              <DifyLogo
                v-else-if="instance?.engine_type === 'DIFY'"
                class="avatar-icon"
              />
              <span v-else class="avatar-text">
                {{ instance?.name?.charAt(0).toUpperCase() }}
              </span>
            </div>
            <div class="header-info">
              <div class="header-name-row">
                <h2 class="header-name">{{ instance?.name }}</h2>
                <!-- 发布态（配置级，静态）：描边轻量，文字/边框取状态色、背景透明，区别于运行态实心 -->
                <el-tag
                  v-if="instance"
                  effect="plain"
                  size="small"
                  class="status-tag publish-tag"
                  :style="{
                    color: statusConfig[instance.status]?.color,
                    borderColor: statusConfig[instance.status]?.color,
                    backgroundColor: 'transparent'
                  }"
                >
                  {{ statusConfig[instance.status]?.label }}
                </el-tag>
                <!-- 运行态（引擎级，动态）：实心 + 状态图标，一眼可辨 -->
                <el-tag
                  v-if="deployStatus"
                  :color="deployStatusConfig[deployStatus.status]?.color"
                  effect="dark"
                  size="small"
                  class="status-tag runtime-tag"
                >
                  <el-icon
                    v-if="deployStatusConfig[deployStatus.status]?.icon"
                    :class="['mr-1', deployStatusConfig[deployStatus.status]?.spin ? 'is-loading' : '']"
                  >
                    <component :is="deployStatusConfig[deployStatus.status]?.icon" />
                  </el-icon>
                  {{ deployStatusConfig[deployStatus.status]?.label }}
                </el-tag>
                <el-tag
                  v-if="restarting"
                  type="warning"
                  effect="light"
                  class="restart-tag"
                >
                  <el-icon class="is-loading mr-1"><RefreshLine /></el-icon>
                  {{ t("instance.detail.restarting") }}
                </el-tag>
              </div>
              <p class="header-desc">
                {{ instance?.description || t("instance.noDescription") }}
              </p>
            </div>
          </div>
        </el-col>
        <el-col :xs="24" :sm="8" class="header-actions">
          <el-tooltip
            v-if="busy"
            :content="t('instance.detail.restartingHint')"
            placement="top"
          >
            <el-button type="primary" plain disabled @click="openEditDialog">
              <el-icon class="mr-1"><EditLine /></el-icon>
              {{ t("common.action.edit") }}
            </el-button>
          </el-tooltip>
          <el-button
            v-else
            type="primary"
            plain
            @click="openEditDialog"
          >
            <el-icon class="mr-1"><EditLine /></el-icon>
            {{ t("common.action.edit") }}
          </el-button>
          <!-- 运行时按钮组 -->
          <template v-if="deployStatus">
            <el-button
              v-if="canResume"
              type="success"
              plain
              :loading="actionLoading === 'resume'"
              :disabled="busy"
              @click="handleResume"
            >
              <el-icon class="mr-1"><PlayLine /></el-icon>
              {{ t("instance.resume") }}
            </el-button>
            <el-button
              v-if="canSuspend"
              type="warning"
              plain
              :loading="actionLoading === 'suspend'"
              :disabled="busy"
              @click="handleSuspend"
            >
              <el-icon class="mr-1"><PauseLine /></el-icon>
              {{ t("agent.detail.suspend") }}
            </el-button>
            <el-button
              v-if="canRestart"
              type="primary"
              plain
              :loading="actionLoading === 'restart'"
              :disabled="busy"
              @click="handleRestart"
            >
              <el-icon class="mr-1"><RefreshLine /></el-icon>
              {{ t("instance.restart") }}
            </el-button>
            <el-button
              v-if="canDestroy"
              type="danger"
              plain
              :loading="actionLoading === 'destroy'"
              :disabled="busy"
              @click="handleDestroy"
            >
              <el-icon class="mr-1"><DeleteBinLine /></el-icon>
              {{ t("instance.destroy") }}
            </el-button>
          </template>
          <el-button
            v-if="canDeploy"
            type="success"
            :loading="actionLoading === 'deploy'"
            :disabled="restarting"
            @click="handleDeploy"
          >
            <el-icon class="mr-1"><RocketLine /></el-icon>
            {{ isDeployed ? t("agent.detail.redeploy") : t("instance.deploy") }}
          </el-button>
          <el-dropdown trigger="click" @command="onMenuCommand">
            <IconifyIconOffline :icon="More2Fill" class="three-dot-btn" />
            <template #dropdown>
              <el-dropdown-menu>
                <el-dropdown-item v-if="canPublish" command="publish">
                  {{ t("instance.online") }}
                </el-dropdown-item>
                <el-dropdown-item v-if="canOffline" command="offline">
                  {{ t("instance.offline") }}
                </el-dropdown-item>
                <el-dropdown-item divided command="delete">
                  <span class="text-red-500">{{ t("common.action.delete") }}</span>
                </el-dropdown-item>
              </el-dropdown-menu>
            </template>
          </el-dropdown>
        </el-col>
      </el-row>
      <!-- 基本信息标签行 -->
      <el-row v-if="instance" :gutter="12" class="header-tags">
        <el-col :span="4" :xs="12" class="tag-item">
          <span class="tag-label">{{ t("instance.detail.label.definition") }}</span>
          <span class="tag-value">
            <el-link
              v-if="instance.definition_id"
              type="primary"
              underline="never"
              @click="goToDefinition"
              style="font-size: 13px; font-weight: 500;"
            >
              {{ instance.definition_name || "—" }}
            </el-link>
            <template v-else>—</template>
          </span>
        </el-col>
        <el-col :span="4" :xs="12" class="tag-item">
          <span class="tag-label">{{ t("instance.detail.label.version") }}</span>
          <span class="tag-value">
            {{ instance.version_no || "—" }}
            <el-tooltip
              v-if="instance.has_newer_version"
              :content="t('instance.version.hasUpdate')"
              placement="top"
            >
              <el-icon
                class="version-upgrade-icon"
                :class="{ 'is-loading': actionLoading === 'upgrade' }"
                @click.stop="handleUpgrade"
              >
                <Notification3Line />
              </el-icon>
            </el-tooltip>
          </span>
        </el-col>
        <el-col :span="4" :xs="12" class="tag-item">
          <span class="tag-label">{{ t("instance.detail.label.resourcePool") }}</span>
          <span class="tag-value">
            <el-link
              v-if="instance.resource_pool_id"
              type="primary"
              underline="never"
              @click="goToResourcePool"
              style="font-size: 13px; font-weight: 500;"
            >
              {{ instance.resource_pool_name || instance.resource_pool_id.slice(0, 8) }}
            </el-link>
            <el-tag
              v-else-if="instance.engine_type === 'DIFY'"
              type="info"
              size="small"
              effect="plain"
            >
              {{ t("agent.difyExternalNoPool") }}
            </el-tag>
            <el-tag v-else type="danger" size="small" effect="plain">
              {{ t("agent.unboundEngine") }}
            </el-tag>
          </span>
        </el-col>
        <el-col :span="4" :xs="12" class="tag-item">
          <span class="tag-label">{{ t("instance.detail.label.engineType") }}</span>
          <span class="tag-value engine-type-value">
            <template v-if="instance.engine_type">
              <HermesLogo
                v-if="instance.engine_type === 'HERMES'"
                class="engine-type-logo"
              />
              <OpenClawLogo
                v-else-if="instance.engine_type === 'OPENCLAW'"
                class="engine-type-logo"
              />
              <DifyLogo
                v-else-if="instance.engine_type === 'DIFY'"
                class="engine-type-logo"
              />
              {{ instance.engine_type }}
            </template>
            <template v-else>—</template>
          </span>
        </el-col>
        <el-col :span="4" :xs="12" class="tag-item">
          <span class="tag-label">{{ t("instance.detail.label.group") }}</span>
          <span class="tag-value">
            {{ instance.group_name || "—" }}
          </span>
        </el-col>
        <el-col :span="4" :xs="12" class="tag-item">
          <span class="tag-label">{{ t("instance.detail.label.creator") }}</span>
          <span class="tag-value">{{ instance.creator_name }}</span>
        </el-col>
        <el-col v-if="isHermes" :span="4" :xs="12" class="tag-item">
          <span class="tag-label">{{ t("instance.detail.label.browserSandbox") }}</span>
          <span class="tag-value">
            <el-tag
              size="small"
              effect="plain"
              :type="browserSandboxEnabled ? 'success' : 'info'"
            >
              {{
                browserSandboxEnabled
                  ? t("instance.detail.browserSandboxEnabled")
                  : t("instance.detail.browserSandboxDisabled")
              }}
            </el-tag>
          </span>
        </el-col>
      </el-row>
      <!-- 智能体ID 行（可复制，用于监控中心等页面过滤） -->
      <el-row v-if="instance" :gutter="12" class="header-tags header-agent-id">
        <el-col :span="24" class="tag-item">
          <span class="tag-label">智能体ID</span>
          <span class="tag-value">
            <el-tooltip :content="instance.id" placement="top" :hide-after="0">
              <span class="font-mono text-xs">{{ instance.id.slice(0, 16) }}…</span>
            </el-tooltip>
            <el-tooltip content="复制完整 ID" placement="top">
              <el-icon class="copy-icon" @click="copyAgentId">
                <FileCopyLine />
              </el-icon>
            </el-tooltip>
          </span>
        </el-col>
      </el-row>
    </el-card>

    <!-- 部署进度面板（deploy 时挂载，轮询 deployment-status 驱动） -->
    <DeployEventsPanel
      v-if="deployPanelActive && instance"
      :deploy-status="deployStatus"
      @retry="onDeployRetry"
    />

    <!-- Tab 面板 -->
    <el-card shadow="never" class="detail-tabs mt-4">
      <el-tabs v-model="activeTab" class="detail-tabs-inner">
        <el-tab-pane :label="t('agent.detail.tabs.overview')" name="overview">
          <OverviewTab
            v-if="instance"
            :instance="instance"
            :deploy-status="deployStatus"
          />
        </el-tab-pane>
        <el-tab-pane :label="t('agent.detail.tabs.runtime')" name="runtime">
          <RuntimeTab v-if="instance" :instance-id="instance.id" :instance="instance" />
        </el-tab-pane>
        <el-tab-pane :label="t('agent.detail.tabs.monitor')" name="monitor">
          <MonitorTab v-if="instance" :instance-id="instance.id" :instance="instance" />
        </el-tab-pane>
        <el-tab-pane :label="t('agent.detail.tabs.memory')" name="memory">
          <MemoryTab />
        </el-tab-pane>
        <el-tab-pane :label="t('agent.detail.tabs.channels')" name="channels">
          <ChannelsTab v-if="instance" :instance-id="instance.id" />
        </el-tab-pane>
        <el-tab-pane :label="t('agent.detail.tabs.apiKeys')" name="apiKeys">
          <ApiKeysTab v-if="instance" :instance-id="instance.id" />
        </el-tab-pane>
      </el-tabs>
    </el-card>
    </div>
  </div>
</template>

<style scoped>
.agent-detail {
  max-width: 1400px;
  margin: 0 auto;
}

.detail-header {
  border-radius: 8px;
}

.header-main {
  display: flex;
  align-items: flex-start;
  gap: 20px;
}

.header-avatar {
  display: flex;
  align-items: center;
  justify-content: center;
  width: 56px;
  height: 56px;
  border-radius: 50%;
  flex-shrink: 0;
}

.avatar-icon {
  width: 28px;
  height: 28px;
}

.avatar-text {
  font-size: 22px;
  font-weight: 600;
}

.header-info {
  flex: 1;
  min-width: 0;
}

.header-name-row {
  display: flex;
  align-items: center;
  gap: 10px;
  margin-bottom: 4px;
  flex-wrap: wrap;
}

.header-name {
  margin: 0;
  font-size: 20px;
  font-weight: 600;
  line-height: 1.3;
}

.header-desc {
  margin: 4px 0 8px;
  font-size: 13px;
  color: var(--el-text-color-secondary);
  line-height: 1.5;
}

.status-tag {
  border: 0;
  height: 22px;
  line-height: 22px;
}

.header-actions {
  display: flex;
  justify-content: flex-end;
  align-items: center;
  gap: 8px;
  flex-wrap: nowrap;
}

.restart-tag {
  display: inline-flex;
  align-items: center;
  font-weight: 500;
}

.three-dot-btn {
  font-size: 22px;
  color: var(--el-text-color-secondary);
  cursor: pointer;
  padding: 4px;
  border-radius: 4px;
  transition: color 0.2s, background 0.2s;
  flex-shrink: 0;
}

.three-dot-btn:hover {
  color: var(--el-color-primary);
  background: var(--el-fill-color-light);
}

.header-tags {
  margin-top: 14px;
  padding-top: 12px;
  border-top: 1px solid var(--el-border-color-light);
}

.header-agent-id {
  margin-top: 8px;
  padding-top: 8px;
}

.header-agent-id .tag-value {
  display: flex;
  align-items: center;
  gap: 6px;
}

.copy-icon {
  cursor: pointer;
  color: var(--el-text-color-placeholder);
  font-size: 14px;
  vertical-align: -2px;
}

.copy-icon:hover {
  color: var(--el-color-primary);
}

.tag-item {
  display: flex;
  flex-direction: column;
  gap: 2px;
  padding: 4px 0;
}

.tag-label {
  font-size: 11px;
  color: var(--el-text-color-placeholder);
  text-transform: uppercase;
  letter-spacing: 0.5px;
}

.tag-value {
  font-size: 13px;
  font-weight: 500;
  color: var(--el-text-color-primary);
}

.version-upgrade-icon {
  margin-left: 4px;
  vertical-align: -2px;
  color: var(--el-color-danger);
  cursor: pointer;
  font-size: 16px;
  animation: version-bell-pulse 1.8s ease-in-out infinite;
}

.version-upgrade-icon:hover {
  color: var(--el-color-danger-light-3);
}

.version-upgrade-icon.is-loading {
  animation: none;
}

@keyframes version-bell-pulse {
  0%,
  100% {
    transform: scale(1);
    opacity: 1;
  }
  50% {
    transform: scale(1.25);
    opacity: 0.65;
  }
}

.engine-type-value {
  display: flex;
  align-items: center;
  gap: 4px;
}

.engine-type-logo {
  width: 16px;
  height: 16px;
  flex-shrink: 0;
}

.detail-tabs {
  border-radius: 8px;
}

.detail-tabs-inner {
  min-height: 400px;
}

:deep(.el-tabs__header) {
  margin-bottom: 20px;
}
</style>
