<script setup lang="ts">
import DocsLink from "@/components/DocsLink/index.vue";
import { useI18n } from "vue-i18n";
import AddFill from "~icons/ri/add-fill";
import SearchLine from "~icons/ri/search-line";
import { useModels } from "./utils/hook";

const { t } = useI18n();
const {
  loading,
  searchText,
  filteredModels,
  stats,
  pagination,
  pagedList,
  providerOf,
  handleSizeChange,
  handleCurrentChange,
  openCreate,
  openEdit,
  openEditPrice,
  handleDelete
} = useModels();
</script>

<template>
  <div class="main" v-loading="loading" :element-loading-text="t('litellm.loading')">
    <DocsLink to="litellm.html#models" />
    <!-- 统计卡 -->
    <el-row :gutter="12" class="mb-1">
      <el-col :span="8">
        <el-card shadow="never" :body-style="{ padding: '6px 12px' }">
          <div class="text-center">
            <div class="text-base font-bold text-primary">{{ stats.total }}</div>
            <div class="text-xs text-gray-400">{{ t("litellm.model.stats.total") }}</div>
          </div>
        </el-card>
      </el-col>
      <el-col :span="8">
        <el-card shadow="never" :body-style="{ padding: '6px 12px' }">
          <div class="text-center">
            <div class="text-base font-bold text-[#00a870]">{{ stats.providers }}</div>
            <div class="text-xs text-gray-400">{{ t("litellm.model.stats.providers") }}</div>
          </div>
        </el-card>
      </el-col>
    </el-row>

    <!-- 工具栏 -->
    <div class="w-full flex flex-wrap items-center justify-between mb-4 gap-3">
      <el-button type="primary" @click="openCreate">
        <AddFill width="18" height="18" class="mr-1" />
        {{ t("litellm.model.create") }}
      </el-button>
      <el-input
        v-model="searchText"
        :placeholder="t('litellm.model.searchPlaceholder')"
        style="width: 260px"
        clearable
        @input="pagination.currentPage = 1"
      >
        <template #suffix>
          <SearchLine v-show="searchText.length === 0" width="18" height="18" class="text-gray-400" />
        </template>
      </el-input>
    </div>

    <el-table :data="pagedList" border stripe :header-cell-style="{ textAlign: 'center' }">
      <el-table-column prop="model_name" :label="t('litellm.model.col.name')" min-width="160" />
      <el-table-column :label="t('litellm.model.col.upstream')" min-width="200">
        <template #default="{ row }">{{ row.litellm_params?.model || "—" }}</template>
      </el-table-column>
      <el-table-column :label="t('litellm.model.col.provider')" min-width="120">
        <template #default="{ row }">
          <el-tag size="small" effect="plain">{{ providerOf(row) }}</el-tag>
        </template>
      </el-table-column>
      <el-table-column :label="t('litellm.model.col.apiBase')" min-width="220">
        <template #default="{ row }">{{ row.litellm_params?.api_base || t("litellm.default") }}</template>
      </el-table-column>
      <el-table-column :label="t('litellm.model.col.contextLength')" min-width="120">
        <template #default="{ row }">{{ row.model_info?.context_length || "—" }}</template>
      </el-table-column>
      <el-table-column :label="t('litellm.model.col.inputPrice')" width="140">
        <template #default="{ row }">
          <el-tag v-if="row.input_cost_per_1m_tokens == null" type="danger" size="small">
            {{ t("litellm.model.tag.priceNotSet") }}
          </el-tag>
          <span v-else>${{ row.input_cost_per_1m_tokens }} / 1M</span>
        </template>
      </el-table-column>
      <el-table-column :label="t('litellm.model.col.outputPrice')" width="140">
        <template #default="{ row }">
          <el-tag v-if="row.output_cost_per_1m_tokens == null" type="danger" size="small">
            {{ t("litellm.model.tag.priceNotSet") }}
          </el-tag>
          <span v-else>${{ row.output_cost_per_1m_tokens }} / 1M</span>
        </template>
      </el-table-column>
      <el-table-column :label="t('litellm.model.col.operation')" width="260" fixed="right">
        <template #default="{ row }">
          <el-button text size="small" @click="openEdit(row)">{{ t("litellm.model.edit") }}</el-button>
          <el-button text size="small" @click="openEditPrice(row)">{{ t("litellm.model.action.editPrice") }}</el-button>
          <el-button text type="danger" size="small" @click="handleDelete(row)">{{ t("common.action.delete") }}</el-button>
        </template>
      </el-table-column>
    </el-table>

    <el-empty v-if="!loading && filteredModels.length === 0" :description="t('litellm.model.empty')" />

    <el-pagination
      v-if="filteredModels.length > 0"
      v-model:current-page="pagination.currentPage"
      class="float-right mt-2"
      :page-size="pagination.pageSize"
      :total="filteredModels.length"
      :page-sizes="[10, 20, 50]"
      :background="pagination.background"
      layout="total, sizes, prev, pager, next, jumper"
      @size-change="handleSizeChange"
      @current-change="handleCurrentChange"
    />
  </div>
</template>
