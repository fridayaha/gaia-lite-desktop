<script setup lang="ts">
import { ref, computed, onMounted } from "vue";
import { useI18n } from "vue-i18n";
import { ElMessage, ElMessageBox } from "element-plus";
import { copyTextToClipboard } from "@pureadmin/utils";
import dayjs from "dayjs";
import {
  getApiKeysApi,
  createApiKeyApi,
  deleteApiKeyApi
} from "@/api/manager/agentInstances";
import type { ApiKeyResponse } from "@/api/manager/agentInstances";

const props = defineProps<{ instanceId: string }>();
const { t } = useI18n();

const loading = ref(false);
const keyList = ref<ApiKeyResponse[]>([]);

// 创建对话框
const createDialogVisible = ref(false);
const createForm = ref({ name: "" });
const creating = ref(false);

// 创建成功后展示明文 key 一次
const createdKeyDialogVisible = ref(false);
const createdKey = ref<{
  id: string;
  name: string;
  key_prefix: string;
  key: string;
  created_at: string;
} | null>(null);
const savedButtonDisabled = ref(true);
let savedButtonTimer: ReturnType<typeof setTimeout> | null = null;

const keyCount = computed(() => keyList.value.length);
const MAX_KEYS = 10;
const canCreate = computed(() => keyCount.value < MAX_KEYS);

async function fetchKeys() {
  loading.value = true;
  try {
    const res = await getApiKeysApi(props.instanceId);
    keyList.value = res.items;
  } catch (e: any) {
    ElMessage.error(
      e?.response?.data?.detail || t("agent.apiKey.msg.loadFailed")
    );
  } finally {
    loading.value = false;
  }
}

function openCreateDialog() {
  if (!canCreate.value) {
    ElMessage.warning(t("agent.apiKey.limitReached"));
    return;
  }
  createForm.value.name = "";
  createDialogVisible.value = true;
}

async function handleCreate() {
  if (!createForm.value.name.trim()) {
    ElMessage.warning(t("agent.apiKey.namePlaceholder"));
    return;
  }
  creating.value = true;
  try {
    const res = await createApiKeyApi(props.instanceId, {
      name: createForm.value.name.trim()
    });
    createdKey.value = res;
    createdKeyDialogVisible.value = true;
    savedButtonDisabled.value = true;
    if (savedButtonTimer) clearTimeout(savedButtonTimer);
    savedButtonTimer = setTimeout(() => {
      savedButtonDisabled.value = false;
    }, 2000);
    createDialogVisible.value = false;
    await fetchKeys();
  } catch (e: any) {
    ElMessage.error(
      e?.response?.data?.detail || t("agent.apiKey.msg.createFailed")
    );
  } finally {
    creating.value = false;
  }
}

function copyCreatedKey() {
  if (!createdKey.value) return;
  if (copyTextToClipboard(createdKey.value.key)) {
    ElMessage.success(t("agent.apiKey.copied"));
  } else {
    ElMessage.error(t("agent.apiKey.msg.copyFailed"));
  }
}

function closeCreatedKeyDialog() {
  createdKeyDialogVisible.value = false;
  createdKey.value = null;
}

async function handleDelete(row: ApiKeyResponse) {
  try {
    await ElMessageBox.confirm(
      t("agent.apiKey.confirmDelete"),
      t("common.action.confirm"),
      { type: "warning" }
    );
  } catch {
    return;
  }
  try {
    await deleteApiKeyApi(props.instanceId, row.id);
    ElMessage.success(t("agent.apiKey.msg.deleteSuccess"));
    await fetchKeys();
  } catch (e: any) {
    ElMessage.error(
      e?.response?.data?.detail || t("agent.apiKey.msg.deleteFailed")
    );
  }
}

function formatTime(t: string | null): string {
  if (!t) return "-";
  return dayjs(t).format("YYYY-MM-DD HH:mm:ss");
}

onMounted(async () => {
  await fetchKeys();
});
</script>

<template>
  <div v-loading="loading" class="api-keys-tab">
    <!-- 操作栏 -->
    <div class="w-full flex flex-wrap items-center justify-between mb-4 gap-3">
      <div class="flex items-center gap-3 flex-wrap">
        <el-button
          type="primary"
          :disabled="!canCreate"
          @click="openCreateDialog"
        >
          {{ t("agent.apiKey.create") }}
        </el-button>
        <span class="text-sm text-gray-500">
          {{ keyCount }} / {{ MAX_KEYS }}
        </span>
      </div>
    </div>

    <!-- Key 列表 -->
    <el-table
      :data="keyList"
      :empty-text="t('agent.apiKey.empty')"
      stripe
    >
      <el-table-column
        :label="t('agent.apiKey.col.name')"
        prop="name"
        min-width="160"
      />
      <el-table-column
        :label="t('agent.apiKey.col.key')"
        min-width="200"
      >
        <template #default="{ row }">
          <code class="text-sm">{{ row.key_prefix }}…</code>
        </template>
      </el-table-column>
      <el-table-column
        :label="t('agent.apiKey.col.createdAt')"
        width="180"
      >
        <template #default="{ row }">
          {{ formatTime(row.created_at) }}
        </template>
      </el-table-column>
      <el-table-column
        :label="t('agent.apiKey.col.lastUsedAt')"
        width="180"
      >
        <template #default="{ row }">
          {{ formatTime(row.last_used_at) }}
        </template>
      </el-table-column>
      <el-table-column
        :label="t('common.action.operation')"
        fixed="right"
        width="100"
      >
        <template #default="{ row }">
          <el-button
            link
            type="primary"
            size="small"
            @click="handleDelete(row)"
          >
            {{ t("common.action.delete") }}
          </el-button>
        </template>
      </el-table-column>
    </el-table>

    <!-- 创建对话框 -->
    <el-dialog
      v-model="createDialogVisible"
      :title="t('agent.apiKey.create')"
      width="420px"
      :close-on-click-modal="false"
    >
      <el-form label-position="top">
        <el-form-item :label="t('agent.apiKey.col.name')">
          <el-input
            v-model="createForm.name"
            :placeholder="t('agent.apiKey.namePlaceholder')"
            maxlength="128"
            show-word-limit
          />
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="createDialogVisible = false">
          {{ t("common.action.cancel") }}
        </el-button>
        <el-button
          type="primary"
          :loading="creating"
          @click="handleCreate"
        >
          {{ t("common.action.confirm") }}
        </el-button>
      </template>
    </el-dialog>

    <!-- 创建成功后展示明文 key（仅一次） -->
    <el-dialog
      v-model="createdKeyDialogVisible"
      :title="t('agent.apiKey.createdTitle')"
      width="560px"
      :close-on-click-modal="false"
      :close-on-press-escape="false"
      :show-close="false"
    >
      <div v-if="createdKey" class="created-key-body">
        <el-alert
          type="warning"
          :title="t('agent.apiKey.copyWarning')"
          :closable="false"
          show-icon
          class="mb-3"
        />
        <div class="created-key-row">
          <code class="created-key-text">{{ createdKey.key }}</code>
          <el-button type="primary" size="small" @click="copyCreatedKey">
            {{ t("agent.apiKey.copy") }}
          </el-button>
        </div>
      </div>
      <template #footer>
        <el-button
          type="primary"
          :disabled="savedButtonDisabled"
          @click="closeCreatedKeyDialog"
        >
          {{ t("agent.apiKey.confirmSaved") }}
        </el-button>
      </template>
    </el-dialog>
  </div>
</template>

<style lang="scss" scoped>
.api-keys-tab {
  padding: 0;
}

.created-key-body {
  display: flex;
  flex-direction: column;
  gap: 8px;
}

.created-key-row {
  display: flex;
  gap: 8px;
  align-items: center;
  background: var(--el-fill-color-light);
  padding: 12px;
  border-radius: 4px;
}

.created-key-text {
  flex: 1;
  word-break: break-all;
  font-family: ui-monospace, "SFMono-Regular", Menlo, Consolas, monospace;
  font-size: 13px;
  user-select: all;
}
</style>
