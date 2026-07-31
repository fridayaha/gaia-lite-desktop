<script setup lang="ts">
import DocsLink from "@/components/DocsLink/index.vue";
import { ref, reactive, onMounted } from "vue";
import { useI18n } from "vue-i18n";
import { ElMessage, ElMessageBox } from "element-plus";
import { copyTextToClipboard } from "@pureadmin/utils";
import type { UploadFile } from "element-plus";

import Image2Line from "~icons/ri/image-2-line?width=28&height=28";

import {
  getAppReleasesApi,
  uploadBaseApkApi,
  updateAppReleaseApi,
  uploadAppReleaseIconApi,
  deleteAppReleaseApi,
  publishAppReleaseApi,
  type AppReleasePlatform,
  type AppReleaseResponse
} from "@/api/manager/appReleases";

defineOptions({ name: "SystemAppRelease" });

const { t } = useI18n();

const loading = ref(false);
const dataList = ref<AppReleaseResponse[]>([]);
const total = ref(0);
const page = ref(1);
const pageSize = ref(20);
const platformFilter = ref<AppReleasePlatform | "">("");

async function fetchList() {
  loading.value = true;
  try {
    const resp = await getAppReleasesApi({
      page: page.value,
      page_size: pageSize.value,
      platform: platformFilter.value || undefined
    });
    dataList.value = resp?.items || [];
    total.value = resp?.total || 0;
  } catch (e: any) {
    ElMessage.error(t("common.msg.loadFailed"));
  } finally {
    loading.value = false;
  }
}

function onPlatformFilterChange() {
  page.value = 1;
  fetchList();
}

function onPageChange(p: number) {
  page.value = p;
  fetchList();
}

function onPageSizeChange(size: number) {
  pageSize.value = size;
  page.value = 1;
  fetchList();
}

onMounted(() => fetchList());

// ── upload base APK ──
async function onApkFileChosen(file: UploadFile) {
  if (!file.raw) return;
  const formData = new FormData();
  formData.append("file", file.raw);
  try {
    const release = await uploadBaseApkApi(formData);
    ElMessage.success(`${t("system.appRelease.uploadSuccess")}（v${release.version}）`);
    fetchList();
  } catch (e: any) {
    ElMessage.error(e?.response?.data?.detail || t("common.msg.operationFailed"));
  }
}

// ── edit dialog ──
const editVisible = ref(false);
const editForm = reactive({ id: "", display_name: "", description: "" });

function openEdit(row: AppReleaseResponse) {
  editForm.id = row.id;
  editForm.display_name = row.display_name;
  editForm.description = row.description;
  editVisible.value = true;
}

async function submitEdit() {
  try {
    await updateAppReleaseApi(editForm.id, {
      display_name: editForm.display_name,
      description: editForm.description
    });
    ElMessage.success(t("common.msg.saveSuccess"));
    editVisible.value = false;
    fetchList();
  } catch (e: any) {
    ElMessage.error(e?.response?.data?.detail || t("common.msg.operationFailed"));
  }
}

// ── icon upload ──
async function onIconChosen(row: AppReleaseResponse, file: UploadFile) {
  if (!file.raw) return;
  const formData = new FormData();
  formData.append("file", file.raw);
  try {
    await uploadAppReleaseIconApi(row.id, formData);
    ElMessage.success(t("system.appRelease.iconOk"));
    fetchList();
  } catch (e: any) {
    ElMessage.error(e?.response?.data?.detail || t("common.msg.operationFailed"));
  }
}

// ── publish dialog ──
const publishVisible = ref(false);
const publishLoading = ref(false);
const publishProgress = ref(0);
let publishTimer: ReturnType<typeof setInterval> | null = null;
const publishForm = reactive({
  id: "",
  manager_url: "",
  gateway_url: ""
});

function openPublish(row: AppReleaseResponse) {
  publishForm.id = row.id;
  // 后端地址静默取当前站点 origin，不在弹窗暴露给用户
  const origin = window.location.origin;
  publishForm.manager_url = `${origin}/api/manager/`;
  publishForm.gateway_url = `${origin}/api/gateway/`;
  publishProgress.value = 0;
  publishVisible.value = true;
}

// 发布是单个长请求（打包+重签名，数十秒），无真实进度可报：
// 模拟进度先快后慢逼近 95%，响应返回后补满 100%
function startPublishProgress() {
  publishProgress.value = 0;
  publishTimer = setInterval(() => {
    const p = publishProgress.value;
    if (p < 95) {
      publishProgress.value = Math.min(95, p + Math.max(0.5, (95 - p) * 0.04));
    }
  }, 500);
}

function stopPublishProgress() {
  if (publishTimer) {
    clearInterval(publishTimer);
    publishTimer = null;
  }
}

async function submitPublish() {
  publishLoading.value = true;
  startPublishProgress();
  try {
    await publishAppReleaseApi(publishForm.id, {
      manager_url: publishForm.manager_url,
      gateway_url: publishForm.gateway_url
    });
    publishProgress.value = 100;
    ElMessage.success(t("system.appRelease.publishSuccess"));
    publishVisible.value = false;
    fetchList();
  } catch (e: any) {
    ElMessage.error(e?.response?.data?.detail || t("common.msg.operationFailed"));
  } finally {
    stopPublishProgress();
    publishLoading.value = false;
  }
}

// ── delete ──
async function onDelete(row: AppReleaseResponse) {
  try {
    await ElMessageBox.confirm(t("system.appRelease.confirmDelete"), {
      type: "warning"
    });
    await deleteAppReleaseApi(row.id);
    ElMessage.success(t("common.msg.deleteSuccess"));
    fetchList();
  } catch (e: any) {
    if (e === "cancel") return;
    ElMessage.error(e?.response?.data?.detail || t("common.msg.deleteFailed"));
  }
}

const statusTagType = (status: string) =>
  status === "published" ? "success" : "info";

const platformTagType = (platform: string) =>
  platform === "harmony" ? "warning" : "primary";

const apkDownloadUrl = (id: string) =>
  `${window.location.origin}/api/manager/public/app-releases/${id}/apk`;

function copyDownloadUrl(id: string) {
  if (copyTextToClipboard(apkDownloadUrl(id))) {
    ElMessage.success(t("common.msg.copied"));
  } else {
    ElMessage.error(t("common.msg.operationFailed"));
  }
}
</script>

<template>
  <div class="main">
    <DocsLink to="system.html#app-release" />
    <div class="w-full flex flex-wrap items-center justify-between mb-4 gap-3">
      <div class="flex items-center gap-3 flex-wrap">
        <el-upload
          accept=".apk,.hap"
          :show-file-list="false"
          :auto-upload="false"
          :on-change="onApkFileChosen"
        >
          <el-button type="primary">
            {{ t("system.appRelease.upload") }}
          </el-button>
        </el-upload>
      </div>
      <div class="flex items-center gap-3 flex-wrap">
        <el-select
          v-model="platformFilter"
          :placeholder="t('system.appRelease.col.platform')"
          clearable
          style="width: 160px"
          @change="onPlatformFilterChange"
        >
          <el-option :label="t('system.appRelease.platformAll')" value="" />
          <el-option :label="t('system.appRelease.platform.android')" value="android" />
          <el-option :label="t('system.appRelease.platform.harmony')" value="harmony" />
        </el-select>
        <el-button :loading="loading" @click="fetchList">
          {{ t("common.action.refresh") }}
        </el-button>
      </div>
    </div>

    <el-table
      v-loading="loading"
      :data="dataList"
      border
      style="width: 100%"
      :empty-text="t('system.appRelease.emptyText')"
    >
      <el-table-column
        :label="t('system.appRelease.col.version')"
        prop="version"
        width="120"
      />
      <el-table-column :label="t('system.appRelease.col.platform')" width="100" align="center">
        <template #default="{ row }">
          <el-tag :type="platformTagType(row.platform)">
            {{ t(`system.appRelease.platform.${row.platform}`) }}
          </el-tag>
        </template>
      </el-table-column>
      <el-table-column :label="t('system.appRelease.col.icon')" width="80" align="center">
        <template #default="{ row }">
          <el-image
            v-if="row.icon_url"
            :src="row.icon_url"
            style="width: 40px; height: 40px"
            fit="cover"
            class="rounded"
          >
            <template #error>
              <Image2Line class="icon-placeholder" />
            </template>
          </el-image>
          <Image2Line v-else class="icon-placeholder" />
        </template>
      </el-table-column>
      <el-table-column
        :label="t('system.appRelease.col.displayName')"
        prop="display_name"
        min-width="120"
      />
      <el-table-column
        :label="t('system.appRelease.col.description')"
        prop="description"
        min-width="200"
        show-overflow-tooltip
      />
      <el-table-column :label="t('system.appRelease.col.status')" width="100">
        <template #default="{ row }">
          <el-tag :type="statusTagType(row.status)">
            {{ t(`system.appRelease.status.${row.status}`) }}
          </el-tag>
        </template>
      </el-table-column>
      <el-table-column
        :label="t('system.appRelease.col.createdAt')"
        prop="created_at"
        width="170"
      >
        <template #default="{ row }">
          {{ row.created_at ? new Date(row.created_at).toLocaleString() : "" }}
        </template>
      </el-table-column>
      <el-table-column
        :label="t('system.appRelease.col.publishedAt')"
        prop="published_at"
        width="170"
      >
        <template #default="{ row }">
          {{ row.published_at ? new Date(row.published_at).toLocaleString() : "—" }}
        </template>
      </el-table-column>
      <el-table-column
        :label="t('system.appRelease.col.operation')"
        min-width="360"
        fixed="right"
        align="center"
      >
        <template #default="{ row }">
          <div class="flex items-center justify-center gap-2 flex-nowrap">
            <el-button link type="primary" @click="openEdit(row)">
              {{ t("system.appRelease.edit") }}
            </el-button>
            <el-upload
              :show-file-list="false"
              :auto-upload="false"
              accept="image/png,image/jpeg,image/webp,image/gif"
              :on-change="(file: UploadFile) => onIconChosen(row, file)"
            >
              <el-button link type="primary">
                {{ t("system.appRelease.iconUpload") }}
              </el-button>
            </el-upload>
            <el-button
              link
              type="primary"
              :disabled="row.status === 'published'"
              @click="openPublish(row)"
            >
              {{ t("system.appRelease.publish") }}
            </el-button>
            <el-button
              v-if="row.status === 'published'"
              link
              type="success"
              @click="copyDownloadUrl(row.id)"
            >
              {{ t("system.appRelease.copyDownloadUrl") }}
            </el-button>
            <el-button link type="danger" @click="onDelete(row)">
              {{ t("system.appRelease.delete") }}
            </el-button>
          </div>
        </template>
      </el-table-column>
    </el-table>

    <div class="flex justify-end mt-4">
      <el-pagination
        v-model:current-page="page"
        v-model:page-size="pageSize"
        :total="total"
        :page-sizes="[10, 20, 50, 100]"
        layout="total, sizes, prev, pager, next, jumper"
        background
        @current-change="onPageChange"
        @size-change="onPageSizeChange"
      />
    </div>

    <!-- edit dialog -->
    <el-dialog
      v-model="editVisible"
      :title="t('system.appRelease.editDialogTitle')"
      width="500px"
    >
      <el-form label-width="100px">
        <el-form-item :label="t('system.appRelease.displayName')">
          <el-input v-model="editForm.display_name" :placeholder="t('system.appRelease.displayNamePlaceholder')" />
        </el-form-item>
        <el-form-item :label="t('system.appRelease.description')">
          <el-input
            v-model="editForm.description"
            type="textarea"
            :rows="3"
            :placeholder="t('system.appRelease.descriptionPlaceholder')"
          />
        </el-form-item>
      </el-form>
      <template #footer>
        <el-button @click="editVisible = false">
          {{ t("common.action.cancel") }}
        </el-button>
        <el-button type="primary" @click="submitEdit">
          {{ t("common.action.save") }}
        </el-button>
      </template>
    </el-dialog>

    <!-- publish dialog -->
    <el-dialog
      v-model="publishVisible"
      :title="t('system.appRelease.publishDialogTitle')"
      width="560px"
      :close-on-click-modal="!publishLoading"
    >
      <el-alert
        :title="t('system.appRelease.publishTip')"
        type="info"
        :closable="false"
        show-icon
        class="mb-3"
      />
      <el-progress
        v-if="publishLoading"
        :percentage="Math.floor(publishProgress)"
        :stroke-width="12"
        striped
        striped-flow
      />
      <template #footer>
        <el-button :disabled="publishLoading" @click="publishVisible = false">
          {{ t("common.action.cancel") }}
        </el-button>
        <el-button type="primary" :loading="publishLoading" @click="submitPublish">
          {{ publishLoading ? t("system.appRelease.publishing") : t("system.appRelease.confirmPublish") }}
        </el-button>
      </template>
    </el-dialog>
  </div>
</template>

<style lang="scss" scoped>
.main-content {
  margin: 24px 24px 0 !important;
}
.icon-placeholder {
  color: #909399;
}
</style>
