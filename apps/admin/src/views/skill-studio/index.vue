<script setup lang="ts">
import { ref, reactive, computed, onMounted } from "vue";
import { useRouter } from "vue-router";
import { useI18n } from "vue-i18n";
import { ElMessage } from "element-plus";
import AddCircleLine from "~icons/ri/add-circle-line";
import SearchLine from "~icons/ri/search-line";
import { useRenderIcon } from "@/components/ReIcon/src/hooks";
import {
  listWorkspacesApi,
  createWorkspaceApi,
  deleteWorkspaceApi,
  type Workspace,
} from "@/api/manager/skill-engine";
import SkillCard from "./components/SkillCard.vue";

defineOptions({ name: "SkillStudioIndex" });

const router = useRouter();
const { t } = useI18n();

const workspaces = ref<Workspace[]>([]);
const loading = ref(false);
const searchText = ref("");

// 前端分页（2A 后端无分页）
const currentPage = ref(1);
const pageSize = ref(12);

const filtered = computed(() => {
  const kw = searchText.value.trim().toLowerCase();
  if (!kw) return workspaces.value;
  return workspaces.value.filter(
    (w) =>
      w.name.toLowerCase().includes(kw) ||
      (w.description || "").toLowerCase().includes(kw),
  );
});
const paged = computed(() =>
  filtered.value.slice(
    (currentPage.value - 1) * pageSize.value,
    currentPage.value * pageSize.value,
  ),
);

async function fetchList() {
  loading.value = true;
  try {
    const res = await listWorkspacesApi();
    workspaces.value = res.workspaces || [];
  } catch {
    workspaces.value = [];
    ElMessage.error(t("hub.studio.msg.loadFailed"));
  } finally {
    loading.value = false;
  }
}

// ── 创建 ──
const createVisible = ref(false);
const createForm = reactive({ name: "", description: "" });
const creating = ref(false);

function openCreate() {
  createForm.name = "";
  createForm.description = "";
  createVisible.value = true;
}

async function doCreate() {
  if (!createForm.name.trim()) {
    ElMessage.warning(t("hub.studio.createDialog.name"));
    return;
  }
  creating.value = true;
  try {
    const ws = await createWorkspaceApi({
      name: createForm.name.trim(),
      description: createForm.description || undefined,
    });
    createVisible.value = false;
    ElMessage.success(t("hub.studio.msg.createSuccess"));
    // 创建后直接进入详情页开发
    router.push(`/skill-studio/detail/${ws.id}`);
  } catch {
    ElMessage.error(t("hub.studio.msg.loadFailed"));
  } finally {
    creating.value = false;
  }
}

async function onDelete(id: string) {
  try {
    await deleteWorkspaceApi(id);
    ElMessage.success(t("hub.studio.msg.deleteSuccess"));
    fetchList();
  } catch {
    ElMessage.error(t("hub.studio.msg.loadFailed"));
  }
}

function onOpen(id: string) {
  router.push(`/skill-studio/detail/${id}`);
}

onMounted(fetchList);

const svg = `
  <path class="path" d="
    M 30 15
    L 28 17
    M 25.61 25.61
    A 15 15, 0, 0, 1, 15 30
    A 15 15, 0, 1, 1, 27.99 7.5
    L 15 15
  " style="stroke-width: 4px; fill: rgba(0, 0, 0, 0)"/>
`;
</script>

<template>
  <div class="main skill-studio-index">
    <!-- 操作栏：创建在左，搜索在右 -->
    <div class="w-full flex flex-wrap items-center justify-between mb-2 gap-3">
      <el-button type="primary" :icon="useRenderIcon(AddCircleLine)" @click="openCreate">
        {{ t("hub.studio.createSkill") }}
      </el-button>
      <div class="flex items-center gap-3 flex-wrap">
        <el-input
          v-model="searchText"
          :placeholder="t('hub.studio.searchPlaceholder')"
          clearable
          style="width: 260px"
          @clear="currentPage = 1"
          @input="currentPage = 1"
        >
          <template #suffix>
            <el-icon class="el-input__icon">
              <SearchLine v-show="searchText.length === 0" />
            </el-icon>
          </template>
        </el-input>
      </div>
    </div>

    <!-- 卡片网格 -->
    <div
      v-loading="loading"
      :element-loading-svg="svg"
      element-loading-svg-view-box="-10, -10, 50, 50"
    >
      <el-empty
        v-if="filtered.length === 0"
        :description="searchText ? t('hub.studio.notFound', { name: searchText }) : t('hub.studio.empty')"
      />

      <template v-if="filtered.length > 0">
        <el-row :gutter="12">
          <el-col
            v-for="ws in paged"
            :key="ws.id"
            :xs="24"
            :sm="12"
            :md="6"
            :lg="6"
          >
            <SkillCard :workspace="ws" @open="onOpen" @delete="onDelete" />
          </el-col>
        </el-row>

        <el-pagination
          v-model:current-page="currentPage"
          v-model:page-size="pageSize"
          class="float-right mt-1"
          :total="filtered.length"
          :page-sizes="[12, 24, 36, 48]"
          :background="true"
          layout="total, sizes, prev, pager, next, jumper"
        />
      </template>
    </div>

    <!-- 创建弹窗 -->
    <el-dialog
      v-model="createVisible"
      :title="t('hub.studio.createDialog.title')"
      width="480px"
      destroy-on-close
    >
      <el-form :model="createForm" label-width="90px" size="default">
        <el-form-item :label="t('hub.studio.createDialog.name')">
          <el-input
            v-model="createForm.name"
            :placeholder="t('hub.studio.createDialog.namePlaceholder')"
          />
        </el-form-item>
        <el-form-item :label="t('hub.studio.createDialog.description')">
          <el-input
            v-model="createForm.description"
            type="textarea"
            :rows="3"
            :placeholder="t('hub.studio.createDialog.descriptionPlaceholder')"
          />
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="createVisible = false">
          {{ t("common.action.cancel") }}
        </el-button>
        <el-button type="primary" :loading="creating" @click="doCreate">
          {{ t("hub.studio.createDialog.confirm") }}
        </el-button>
      </template>
    </el-dialog>
  </div>
</template>

<style scoped>
.skill-studio-index {
  --ss-ink: #1f2430;
  --ss-line: #e5e7eb;
  --ss-accent: #6d5efc;
}
</style>
