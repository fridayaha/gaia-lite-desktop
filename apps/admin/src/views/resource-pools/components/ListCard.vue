<script setup lang="ts">
import type { ResourcePoolResponse } from "@/api/manager/resourcePools";
import { useI18n } from "vue-i18n";
import More2Fill from "~icons/ri/more-2-fill";
import { useRouter } from "vue-router";

const props = defineProps<{ pool: ResourcePoolResponse }>();
const emit = defineEmits<{
  edit: [pool: ResourcePoolResponse];
  clone: [pool: ResourcePoolResponse];
  delete: [pool: ResourcePoolResponse];
}>();

const { t } = useI18n();
const router = useRouter();

function goDetail() {
  router.push(`/resource-pools/detail/${props.pool.id}`);
}

function onCommand(cmd: string) {
  switch (cmd) {
    case "edit": emit("edit", props.pool); break;
    case "clone": emit("clone", props.pool); break;
    case "delete": emit("delete", props.pool); break;
  }
}
</script>

<template>
  <el-card shadow="hover" class="pool-card" @click="goDetail">
    <!-- Header -->
    <div class="flex items-start justify-between mb-3">
      <div class="flex items-center gap-2 min-w-0 flex-1">
        <div
          class="w-10 h-10 rounded-lg flex items-center justify-center text-white text-sm font-bold shrink-0 bg-blue-500"
        >
          {{ pool.name?.charAt(0).toUpperCase() || "P" }}
        </div>
        <div class="min-w-0">
          <p class="font-medium text-sm truncate">{{ pool.name }}</p>
          <p class="text-xs text-gray-400 truncate">{{ pool.instance_count }} 个实例</p>
        </div>
      </div>
      <el-dropdown trigger="click" @command="onCommand">
        <el-button text size="small" @click.stop>
          <More2Fill width="16" height="16" class="text-gray-400" />
        </el-button>
        <template #dropdown>
          <el-dropdown-menu>
            <el-dropdown-item command="edit">{{ t("common.action.edit") }}</el-dropdown-item>
            <el-dropdown-item command="clone">{{ t("common.action.clone") }}</el-dropdown-item>
            <el-dropdown-item command="delete" class="text-red-500">
              {{ t("common.action.delete") }}
            </el-dropdown-item>
          </el-dropdown-menu>
        </template>
      </el-dropdown>
    </div>

    <!-- Specs -->
    <div class="specs-grid">
      <div class="spec-item">
        <span class="spec-label">CPU</span>
        <span class="spec-value">{{ pool.min_cpu }} ~ {{ pool.max_cpu }}</span>
      </div>
      <div class="spec-item">
        <span class="spec-label">{{ t("engine.card.memory") }}</span>
        <span class="spec-value">{{ pool.min_memory }} ~ {{ pool.max_memory }}</span>
      </div>
      <div class="spec-item">
        <span class="spec-label">{{ t("engine.card.replicas") }}</span>
        <span class="spec-value">{{ pool.min_replicas }} ~ {{ pool.max_replicas }}</span>
      </div>
      <div class="spec-item">
        <span class="spec-label">{{ t("engine.detail.label.sessionPerPod") }}</span>
        <span class="spec-value">{{ pool.max_sessions_per_pod }}</span>
      </div>
    </div>

    <!-- Footer -->
    <div class="flex items-center justify-between mt-3 pt-3 border-t border-gray-100">
      <div class="flex items-center gap-1">
        <el-tag
          :type="pool.auto_recycle ? 'success' : 'info'"
          size="small"
          effect="plain"
        >
          {{ pool.auto_recycle ? t('engine.autoRecycle') : t('engine.manualManage') }}
        </el-tag>
      </div>
      <span class="text-xs text-gray-400">{{ pool.creator_name }}</span>
    </div>
  </el-card>
</template>

<style scoped>
.pool-card {
  cursor: pointer;
  border-radius: 8px;
  transition: box-shadow 0.2s;
}
.pool-card:hover {
  box-shadow: 0 4px 16px rgba(0, 0, 0, 0.08);
}
.specs-grid {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 6px 12px;
}
.spec-item {
  display: flex;
  justify-content: space-between;
  align-items: center;
}
.spec-label {
  font-size: 12px;
  color: #909399;
}
.spec-value {
  font-size: 12px;
  font-weight: 500;
  color: #303133;
  font-family: monospace;
}
</style>
