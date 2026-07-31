<script setup lang="ts">
import DocsLink from "@/components/DocsLink/index.vue";
import { ref, onMounted } from "vue";
import { useRouter } from "vue-router";
import { useI18n } from "vue-i18n";
import { ElMessageBox } from "element-plus";
import {
  getPendingArticlesApi,
  auditArticleApi,
  type ArticleListItem
} from "@/api/manager/community";
import { message } from "@/utils/message";
import AuditLine from "~icons/ri/git-pull-request-line";
import CheckLine from "~icons/ri/check-line";
import CloseLine from "~icons/ri/close-line";

defineOptions({ name: "CommunityAudit" });

const router = useRouter();
const { t } = useI18n();

const loading = ref(false);
const items = ref<ArticleListItem[]>([]);
const total = ref(0);
const page = ref(1);
const pageSize = ref(10);
const approvingId = ref<string>("");
const rejectingId = ref<string>("");

async function fetchList() {
  loading.value = true;
  try {
    const res = await getPendingArticlesApi({
      page: page.value,
      page_size: pageSize.value
    });
    items.value = res.items || [];
    total.value = res.total || 0;
  } catch (e: any) {
    message(t("community.msg.loadFailed"), { type: "error" });
  } finally {
    loading.value = false;
  }
}

function onPageChange(p: number) {
  page.value = p;
  fetchList();
}

async function approve(item: ArticleListItem) {
  approvingId.value = item.id;
  try {
    await auditArticleApi(item.id, { approve: true });
    message(t("community.msg.approveSuccess"), { type: "success" });
    await fetchList();
  } catch (e: any) {
    message(e?.response?.data?.detail || "Approve failed", { type: "error" });
  } finally {
    approvingId.value = "";
  }
}

async function reject(item: ArticleListItem) {
  let reason = "";
  try {
    const result = await ElMessageBox.prompt(
      t("community.rejectReasonPlaceholder"),
      t("community.btnReject"),
      {
        confirmButtonText: t("community.btnReject"),
        cancelButtonText: "Cancel",
        inputType: "textarea",
        inputPlaceholder: t("community.rejectReasonPlaceholder"),
        inputValidator: v => (v && v.trim().length > 0) || t("community.msg.rejectReasonRequired")
      }
    );
    reason = (result.value || "").trim();
  } catch {
    return;
  }
  rejectingId.value = item.id;
  try {
    await auditArticleApi(item.id, { approve: false, reject_reason: reason });
    message(t("community.msg.rejectSuccess"), { type: "success" });
    await fetchList();
  } catch (e: any) {
    message(e?.response?.data?.detail || "Reject failed", { type: "error" });
  } finally {
    rejectingId.value = "";
  }
}

function openDetail(item: ArticleListItem) {
  router.push(`/community/${item.slug}`);
}

function formatDateTime(s: string | null): string {
  if (!s) return "";
  const d = new Date(s);
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(
    d.getDate()
  ).padStart(2, "0")} ${String(d.getHours()).padStart(2, "0")}:${String(
    d.getMinutes()
  ).padStart(2, "0")}`;
}

onMounted(() => {
  fetchList();
});
</script>

<template>
  <div class="main">
    <DocsLink to="community.html#audit" />
    <div class="w-full flex flex-wrap items-center justify-between mb-4 gap-3">
      <div class="flex items-center gap-3 flex-wrap">
        <AuditLine width="20" height="20" />
        <h2 class="text-xl font-semibold m-0">{{ t("community.auditTitle") }}</h2>
      </div>
    </div>

    <el-table v-loading="loading" :data="items" stripe class="w-full">
      <el-table-column :label="t('community.titleLabel')" min-width="220">
        <template #default="{ row }">
          <el-link type="primary" @click="openDetail(row)">{{ row.title }}</el-link>
        </template>
      </el-table-column>
      <el-table-column :label="t('community.byAuthor')" width="140">
        <template #default="{ row }">{{ row.author_name || "—" }}</template>
      </el-table-column>
      <el-table-column label="Slug" width="200" prop="slug" />
      <el-table-column label="Submitted" width="160">
        <template #default="{ row }">{{ formatDateTime(row.created_at) }}</template>
      </el-table-column>
      <el-table-column :label="t('community.btnAudit')" width="200" fixed="right">
        <template #default="{ row }">
          <el-button
            type="success"
            size="small"
            :loading="approvingId === row.id"
            @click="approve(row)"
          >
            <CheckLine width="14" height="14" class="mr-1" />
            {{ t("community.btnApprove") }}
          </el-button>
          <el-button
            type="danger"
            size="small"
            plain
            :loading="rejectingId === row.id"
            @click="reject(row)"
          >
            <CloseLine width="14" height="14" class="mr-1" />
            {{ t("community.btnReject") }}
          </el-button>
        </template>
      </el-table-column>
    </el-table>

    <div v-if="total > pageSize" class="w-full flex justify-center mt-4">
      <el-pagination
        background
        layout="prev, pager, next"
        :total="total"
        :page-size="pageSize"
        :current-page="page"
        @current-change="onPageChange"
      />
    </div>
  </div>
</template>
