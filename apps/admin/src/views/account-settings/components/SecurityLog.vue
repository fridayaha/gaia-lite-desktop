<script setup lang="ts">
import dayjs from "dayjs";
import { getMineLogs } from "@/api/user";
import { reactive, ref, onMounted, computed } from "vue";
import { useI18n } from "vue-i18n";
import { deviceDetection } from "@pureadmin/utils";
import type { PaginationProps } from "@pureadmin/table";

defineOptions({
  name: "SecurityLog"
});

const { t, te } = useI18n();
const loading = ref(true);
const dataList = ref([]);
const pagination = reactive<PaginationProps>({
  total: 0,
  pageSize: 10,
  currentPage: 1,
  background: true,
  layout: "prev, pager, next"
});

// action 翻译：只显 verb（登录/登出/修改密码/修改资料），找不到时 fallback 原值。
// 安全日志只展示 4 类账户安全操作，domain 前缀信息量低，简洁优先。
function formatAction(action: string): string {
  if (!action) return "";
  const parts = action.split(".");
  if (parts.length === 2) {
    const vKey = `operationLog.actionVerb.${parts[1]}`;
    return te(vKey) ? t(vKey) : parts[1];
  }
  return action;
}

const columns = computed<TableColumnList>(() => [
  {
    label: t("account.securityLog.col.action"),
    prop: "action",
    minWidth: 140,
    formatter: ({ action }) => formatAction(action || "")
  },
  {
    label: t("account.securityLog.col.ip"),
    prop: "operator_ip",
    minWidth: 120
  },
  {
    label: t("account.securityLog.col.ua"),
    prop: "operator_user_agent",
    minWidth: 200,
    showOverflowTooltip: true,
    formatter: ({ operator_user_agent }) => operator_user_agent || "—"
  },
  {
    label: t("account.securityLog.col.time"),
    prop: "created_at",
    minWidth: 180,
    formatter: ({ created_at }) =>
      dayjs(created_at).format("YYYY-MM-DD HH:mm:ss")
  }
]);

async function onSearch() {
  loading.value = true;
  const { code, data } = await getMineLogs();
  if (code === 0) {
    dataList.value = data.list;
    pagination.total = data.total;
    pagination.pageSize = data.pageSize;
    pagination.currentPage = data.currentPage;
  }

  setTimeout(() => {
    loading.value = false;
  }, 200);
}

onMounted(() => {
  onSearch();
});
</script>

<template>
  <div :class="['min-w-45', deviceDetection() ? 'max-w-full' : 'max-w-[70%]']">
    <h3 class="my-8!">{{ t("account.securityLog.title") }}</h3>
    <p class="mb-2 text-xs text-gray-500">{{ t("account.securityLog.hint") }}</p>
    <pure-table
      row-key="id"
      table-layout="auto"
      :loading="loading"
      :data="dataList"
      :columns="columns"
      :pagination="pagination"
    />
  </div>
</template>
