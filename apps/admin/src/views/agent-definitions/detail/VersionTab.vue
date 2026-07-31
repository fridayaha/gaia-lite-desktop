<script setup lang="ts">
import { ref, computed, onMounted } from "vue";
import { useI18n } from "vue-i18n";
import dayjs from "dayjs";
import { getVersionsApi, type AgentVersionResponse } from "@/api/manager/agentDefinitions";
import { message } from "@/utils/message";
import CheckLine from "~icons/ri/check-line";
import HistoryLine from "~icons/ri/history-line";

defineOptions({ name: "DefinitionVersionTab" });

const props = defineProps<{
  definitionId: string;
  currentVersionId: string | null;
}>();

const { t } = useI18n();

const loading = ref(false);
const versions = ref<AgentVersionResponse[]>([]);

const sortedVersions = computed(() =>
  [...versions.value].sort(
    (a, b) => new Date(b.created_at).getTime() - new Date(a.created_at).getTime()
  )
);

async function fetchData() {
  loading.value = true;
  try {
    const res = await getVersionsApi(props.definitionId);
    versions.value = (res as any) || [];
  } catch (err: any) {
    message(err?.response?.data?.detail || t("common.msg.loadFailed"), { type: "error" });
    versions.value = [];
  } finally {
    loading.value = false;
  }
}

onMounted(fetchData);
</script>

<template>
  <div class="version-tab">
    <div class="version-header">
      <div class="version-header-title">
        <el-icon class="mr-2"><HistoryLine /></el-icon>
        {{ t("definition.version.title") }}
      </div>
      <span class="version-header-hint">{{ t("definition.version.empty") }}</span>
    </div>

    <div v-loading="loading">
      <el-empty v-if="!loading && sortedVersions.length === 0" :description="t('definition.version.empty')" />

      <el-timeline v-else class="version-timeline">
        <el-timeline-item
          v-for="v in sortedVersions"
          :key="v.id"
          :timestamp="v.created_at ? dayjs(v.created_at).format('YYYY-MM-DD HH:mm:ss') : '-'"
          placement="top"
          :hollow="v.id !== currentVersionId"
          :type="v.id === currentVersionId ? 'success' : 'primary'"
        >
          <el-card shadow="never" class="version-item" :class="{ 'version-current': v.id === currentVersionId }">
            <div class="version-item-header">
              <span class="version-no">v{{ v.version_no }}</span>
              <el-tag v-if="v.id === currentVersionId" type="success" effect="dark" size="small" class="current-tag">
                <el-icon class="mr-1"><CheckLine /></el-icon>
                {{ t("definition.version.current") }}
              </el-tag>
            </div>
            <p v-if="v.change_log" class="version-change-log">{{ v.change_log }}</p>
            <p v-else class="version-change-log text-gray-400">{{ t("definition.version.changeLog") }}</p>
          </el-card>
        </el-timeline-item>
      </el-timeline>

      <el-alert
        v-if="sortedVersions.length > 0"
        type="info"
        :closable="false"
        show-icon
        class="version-footer-tip"
      >
        {{ t("definition.version.empty") }}
      </el-alert>
    </div>
  </div>
</template>

<style scoped>
.version-tab {
  margin-bottom: 20px;
}

.version-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  margin-bottom: 16px;
}

.version-header-title {
  display: flex;
  align-items: center;
  font-size: 15px;
  font-weight: 600;
  color: var(--el-text-color-primary);
}

.version-header-hint {
  font-size: 12px;
  color: var(--el-text-color-placeholder);
}

.version-timeline {
  padding-left: 8px;
}

.version-item {
  border-radius: 8px;
}

.version-current {
  border-color: var(--el-color-success-light-5);
}

.version-item-header {
  display: flex;
  align-items: center;
  gap: 10px;
  margin-bottom: 6px;
}

.version-no {
  font-size: 15px;
  font-weight: 600;
  color: var(--el-text-color-primary);
}

.current-tag {
  display: inline-flex;
  align-items: center;
}

.version-change-log {
  margin: 0;
  font-size: 13px;
  color: var(--el-text-color-regular);
  line-height: 1.5;
  white-space: pre-wrap;
}

.version-footer-tip {
  margin-top: 16px;
  border-radius: 8px;
}
</style>
