<script setup lang="ts">
import DocsLink from "@/components/DocsLink/index.vue";
import { useI18n } from "vue-i18n";
import SearchLine from "~icons/ri/search-line";
import { useKeys } from "./utils/hook";

const { t } = useI18n();
const {
  loading,
  groups,
  groupFilter,
  searchText,
  filteredKeys,
  stats,
  pagination,
  pagedList,
  groupName,
  agentName,
  handleSizeChange,
  handleCurrentChange,
  handleToggleBlock,
  handleDelete,
  openEdit,
  loadKeys
} = useKeys();
</script>

<template>
  <div class="main" v-loading="loading" :element-loading-text="t('litellm.loading')">
    <DocsLink to="litellm.html#keys" />
    <!-- 统计卡 -->
    <el-row :gutter="12" class="mb-1">
      <el-col :span="8">
        <el-card shadow="never" :body-style="{ padding: '6px 12px' }">
          <div class="text-center">
            <div class="text-base font-bold text-primary">{{ stats.total }}</div>
            <div class="text-xs text-gray-400">{{ t("litellm.key.stats.total") }}</div>
          </div>
        </el-card>
      </el-col>
      <el-col :span="8">
        <el-card shadow="never" :body-style="{ padding: '6px 12px' }">
          <div class="text-center">
            <div class="text-base font-bold text-[#00a870]">{{ stats.normal }}</div>
            <div class="text-xs text-gray-400">{{ t("litellm.key.stats.normal") }}</div>
          </div>
        </el-card>
      </el-col>
      <el-col :span="8">
        <el-card shadow="never" :body-style="{ padding: '6px 12px' }">
          <div class="text-center">
            <div class="text-base font-bold text-[#f56c6c]">{{ stats.blocked }}</div>
            <div class="text-xs text-gray-400">{{ t("litellm.key.stats.blocked") }}</div>
          </div>
        </el-card>
      </el-col>
    </el-row>

    <!-- 工具栏：监控页无新建，左侧留空说明，右侧筛选+搜索 -->
    <div class="w-full flex flex-wrap items-center justify-between mb-4 gap-3">
      <span class="text-sm text-gray-400">{{ t("litellm.key.title") }}</span>
      <div class="flex items-center gap-3 flex-wrap">
        <el-select
          v-model="groupFilter"
          :placeholder="t('litellm.key.groupFilterPlaceholder')"
          clearable
          style="width: 180px"
          @change="pagination.currentPage = 1; loadKeys()"
        >
          <el-option :label="t('litellm.platformDefault')" value="default" />
          <el-option v-for="g in groups" :key="g.id" :label="g.name" :value="g.id" />
        </el-select>
        <el-input
          v-model="searchText"
          :placeholder="t('litellm.key.searchPlaceholder')"
          style="width: 260px"
          clearable
          @input="pagination.currentPage = 1"
        >
          <template #suffix>
            <SearchLine v-show="searchText.length === 0" width="18" height="18" class="text-gray-400" />
          </template>
        </el-input>
      </div>
    </div>

    <el-table :data="pagedList" border stripe :header-cell-style="{ textAlign: 'center' }">
      <el-table-column :label="t('litellm.key.col.agent')" min-width="160">
        <template #default="{ row }">{{ agentName(row) }}</template>
      </el-table-column>
      <el-table-column :label="t('litellm.key.col.group')" min-width="140">
        <template #default="{ row }">{{ groupName(row.team_id) }}</template>
      </el-table-column>
      <el-table-column :label="t('litellm.key.col.models')" min-width="180">
        <template #default="{ row }">
          <el-tag v-for="m in row.models || []" :key="m" size="small" class="mr-1">{{ m }}</el-tag>
          <span v-if="!row.models || row.models.length === 0">{{ t("litellm.key.allModels") }}</span>
        </template>
      </el-table-column>
      <el-table-column :label="t('litellm.key.col.budget')" width="130">
        <template #default="{ row }">
          <span v-if="row.max_budget != null">${{ row.max_budget }}</span>
          <span v-else>—</span>
          <span v-if="row.budget_duration" class="text-gray-400 text-xs">/{{ row.budget_duration }}</span>
        </template>
      </el-table-column>
      <el-table-column :label="t('litellm.key.col.spend')" width="110">
        <template #default="{ row }">¥{{ Number(row.spend || 0).toFixed(4) }}</template>
      </el-table-column>
      <el-table-column :label="t('litellm.key.col.status')" width="90">
        <template #default="{ row }">
          <el-tag v-if="row.blocked" type="danger" size="small">{{ t("litellm.key.status.blocked") }}</el-tag>
          <el-tag v-else type="success" size="small">{{ t("litellm.key.status.normal") }}</el-tag>
        </template>
      </el-table-column>
      <el-table-column :label="t('litellm.key.col.operation')" width="220" fixed="right">
        <template #default="{ row }">
          <el-button text size="small" @click="openEdit(row)">{{ t("litellm.key.action.edit") }}</el-button>
          <el-button text size="small" @click="handleToggleBlock(row)">
            {{ row.blocked ? t("litellm.key.action.unblock") : t("litellm.key.action.block") }}
          </el-button>
          <el-button text type="danger" size="small" @click="handleDelete(row)">{{ t("litellm.key.action.revoke") }}</el-button>
        </template>
      </el-table-column>
    </el-table>

    <el-empty v-if="!loading && filteredKeys.length === 0" :description="t('litellm.key.empty')" />

    <el-pagination
      v-if="filteredKeys.length > 0"
      v-model:current-page="pagination.currentPage"
      class="float-right mt-2"
      :page-size="pagination.pageSize"
      :total="filteredKeys.length"
      :page-sizes="[10, 20, 50]"
      :background="pagination.background"
      layout="total, sizes, prev, pager, next, jumper"
      @size-change="handleSizeChange"
      @current-change="handleCurrentChange"
    />
  </div>
</template>
