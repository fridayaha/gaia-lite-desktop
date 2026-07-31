<script setup lang="ts">
import { ref, computed, onMounted } from "vue";
import { useRoute, useRouter } from "vue-router";
import { useI18n } from "vue-i18n";
import {
  getResourcePoolApi,
  deleteResourcePoolApi,
  cloneResourcePoolApi,
  type ResourcePoolResponse
} from "@/api/manager/resourcePools";
import { openResourcePoolDialog } from "../utils/hook";
import { message } from "@/utils/message";
import PodsTab from "./PodsTab.vue";
import MonitorTab from "./MonitorTab.vue";
import LogsTab from "./LogsTab.vue";
import ServerLine from "~icons/ri/server-line";
import More2Fill from "~icons/ri/more-2-fill";

defineOptions({ name: "ResourcePoolDetail" });

const route = useRoute();
const router = useRouter();
const { t } = useI18n();

const poolId = computed(() => route.params.id as string);
const activeTab = ref("pods");
const preselectedPod = ref("");
const loading = ref(true);
const pool = ref<ResourcePoolResponse | null>(null);

async function fetchPool() {
  loading.value = true;
  try {
    pool.value = await getResourcePoolApi(poolId.value);
  } catch {
    message(t("engine.msg.loadFailed"), { type: "error" });
    router.back();
  } finally {
    loading.value = false;
  }
}

function goEdit() {
  if (!pool.value) return;
  openResourcePoolDialog("edit", { ...pool.value }, fetchPool);
}

async function handleClone() {
  if (!pool.value) return;
  try {
    await cloneResourcePoolApi(pool.value.id);
    message(t("engine.msg.cloneOk"), { type: "success" });
  } catch (e: any) {
    message(t("engine.msg.cloneFailed", { detail: e?.message || e }), { type: "error" });
  }
}

async function handleDelete() {
  if (!pool.value) return;
  try {
    await deleteResourcePoolApi(pool.value.id);
    message(t("engine.msg.deleteOk"), { type: "success" });
    router.push("/resource-pools/index");
  } catch (e: any) {
    message(t("engine.msg.deleteFailed", { detail: e?.response?.data?.detail || e?.message || e }), { type: "error" });
  }
}

function onCommand(cmd: string) {
  switch (cmd) {
    case "edit": goEdit(); break;
    case "clone": handleClone(); break;
    case "delete": handleDelete(); break;
  }
}

function onViewPodLogs(podName: string) {
  preselectedPod.value = podName;
  activeTab.value = "logs";
}

onMounted(fetchPool);
</script>

<template>
  <div v-loading="loading" class="main">
    <div class="pool-detail">
    <!-- Header Card -->
    <el-card v-if="pool" shadow="never" class="detail-header">
      <div class="flex items-start justify-between">
        <div class="flex items-center gap-4 min-w-0">
          <div class="w-9 h-9 rounded-lg flex items-center justify-center text-blue-500 shrink-0">
            <ServerLine width="28" height="28" />
          </div>
          <div class="min-w-0">
            <div class="flex items-center gap-2 flex-wrap">
              <h2 class="text-base font-semibold m-0 leading-6">{{ pool.name }}</h2>
              <el-tag
                :type="pool.auto_recycle ? 'success' : 'info'"
                size="small"
                effect="plain"
                style="height: 20px; line-height: 20px; font-size: 11px;"
              >
                {{ pool.auto_recycle ? t('engine.autoRecycle') : t('engine.manualManage') }}
              </el-tag>
              <el-tag type="info" size="small" effect="plain" style="height: 20px; line-height: 20px; font-size: 11px;">
                {{ pool.instance_count }} 个实例
              </el-tag>
              <span class="text-xs text-gray-400 truncate max-w-[300px]">{{ pool.description || '' }}</span>
            </div>
            <!-- 配置摘要 -->
            <div class="flex items-center gap-4 flex-wrap text-xs mt-2">
              <span class="text-gray-400">CPU</span>
              <span>{{ pool.min_cpu }} ~ {{ pool.max_cpu }}</span>
              <span class="text-gray-400">{{ t("engine.card.memory") }}</span>
              <span>{{ pool.min_memory }} ~ {{ pool.max_memory }}</span>
              <span class="text-gray-400">{{ t("engine.card.replicas") }}</span>
              <span>{{ pool.min_replicas }} ~ {{ pool.max_replicas }}</span>
              <span class="text-gray-400">{{ t("engine.detail.label.sessionPerPod") }}</span>
              <span>{{ pool.max_sessions_per_pod }}</span>
              <template v-if="pool.auto_recycle">
                <span class="text-gray-400">{{ t("engine.detail.label.idleSuspend") }}</span>
                <span>{{ pool.idle_suspend_minutes }}m</span>
                <span class="text-gray-400">{{ t("engine.detail.label.destroy") }}</span>
                <span>{{ pool.idle_destroy_hours }}h</span>
              </template>
              <span class="text-gray-400">{{ t("engine.detail.label.creator") }}</span>
              <span>{{ pool.creator_name }}</span>
            </div>
          </div>
        </div>
        <!-- 操作按钮 -->
        <div class="flex items-center gap-2 shrink-0 ml-3">
          <el-button size="small" plain @click="goEdit">{{ t("common.action.edit") }}</el-button>
          <el-dropdown trigger="click" @command="onCommand">
            <el-button text size="small">
              <More2Fill width="20" height="20" class="three-dot-btn" />
            </el-button>
            <template #dropdown>
              <el-dropdown-menu>
                <el-dropdown-item command="clone">{{ t("common.action.clone") }}</el-dropdown-item>
                <el-dropdown-item command="delete" class="text-red-500">
                  {{ t("common.action.delete") }}
                </el-dropdown-item>
              </el-dropdown-menu>
            </template>
          </el-dropdown>
        </div>
      </div>
    </el-card>

    <!-- Tab 面板 -->
    <el-card v-if="pool" shadow="never" class="detail-tabs mt-4">
      <el-tabs v-model="activeTab">
        <el-tab-pane :label="t('engine.detail.tabs.pods')" name="pods">
          <PodsTab v-if="activeTab === 'pods'" :pool-id="pool.id" @view-logs="onViewPodLogs" />
        </el-tab-pane>
        <el-tab-pane :label="t('engine.detail.tabs.monitor')" name="monitor">
          <MonitorTab v-if="activeTab === 'monitor'" :pool-id="pool.id" />
        </el-tab-pane>
        <el-tab-pane :label="t('engine.detail.tabs.logs')" name="logs">
          <LogsTab v-if="activeTab === 'logs'" :pool-id="pool.id" :initial-pod-name="preselectedPod" />
        </el-tab-pane>
      </el-tabs>
    </el-card>
    </div>
  </div>
</template>

<style scoped>
.pool-detail {
  max-width: 1400px;
  margin: 0 auto;
}

.detail-header {
  border-radius: 8px;
}

.detail-tabs {
  border-radius: 8px;
}

.three-dot-btn {
  color: var(--el-text-color-secondary);
  cursor: pointer;
  padding: 2px;
  border-radius: 4px;
  transition: color 0.2s, background 0.2s;
}

.three-dot-btn:hover {
  color: var(--el-color-primary);
  background: var(--el-fill-color-light);
}

:deep(.el-tabs__header) {
  margin-bottom: 14px;
}
</style>
