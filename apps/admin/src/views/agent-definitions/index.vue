<script setup lang="ts">
import DocsLink from "@/components/DocsLink/index.vue";
import { useDefinition } from "./utils/hook";
import ListCard from "./components/ListCard.vue";
import { useRenderIcon } from "@/components/ReIcon/src/hooks";
import { useI18n } from "vue-i18n";
import AddFill from "~icons/ri/add-circle-line";
import SearchLine from "~icons/ri/search-line";

defineOptions({ name: "AgentDefinitionList" });
const { t } = useI18n();

const {
  loading,
  pagedList,
  filteredDefinitions,
  pagination,
  searchText,
  statusFilter,
  engineFilter,
  publishedCount,
  draftCount,
  onSearch,
  openDialog,
  handlePublishVersion,
  handleDelete,
  handleSizeChange,
  handleCurrentChange
} = useDefinition();

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

function onEditDefinition(def: any) {
  openDialog("edit", def);
}
</script>

<template>
  <div class="main">
    <DocsLink to="agent-definitions.html" />
    <!-- Stats Cards -->
    <el-row :gutter="12" class="mb-1">
      <el-col :span="12">
        <el-card shadow="never" :body-style="{ padding: '6px 12px' }">
          <div class="text-center">
            <div class="text-base font-bold text-primary">{{ publishedCount }}</div>
            <div class="text-xs text-gray-400">{{ t("definition.stats.published") }}</div>
          </div>
        </el-card>
      </el-col>
      <el-col :span="12">
        <el-card shadow="never" :body-style="{ padding: '6px 12px' }">
          <div class="text-center">
            <div class="text-base font-bold text-[#f59e0b]">{{ draftCount }}</div>
            <div class="text-xs text-gray-400">{{ t("definition.stats.draft") }}</div>
          </div>
        </el-card>
      </el-col>
    </el-row>

    <!-- Toolbar: left button + right search/filters -->
    <div class="w-full flex flex-wrap items-center justify-between mb-2 gap-3">
      <el-button
        type="primary"
        :icon="useRenderIcon(AddFill)"
        @click="openDialog()"
      >
        {{ t("definition.create") }}
      </el-button>
      <div class="flex items-center gap-3 flex-wrap">
        <el-select
          v-model="statusFilter"
          :placeholder="t('agent.filter.allStatus')"
          clearable
          class="w-32!"
          @change="handleCurrentChange(1)"
        >
          <el-option :label="t('agent.filter.allStatus')" value="" />
          <el-option :label="t('common.status.draft')" value="DRAFT" />
          <el-option :label="t('common.status.published')" value="PUBLISHED" />
        </el-select>
        <el-select
          v-model="engineFilter"
          :placeholder="t('agent.filter.allEngine')"
          clearable
          class="w-32!"
          @change="handleCurrentChange(1)"
        >
          <el-option :label="t('agent.filter.allEngine')" value="" />
          <el-option label="Hermes" value="HERMES" />
          <el-option label="OpenClaw" value="OPENCLAW" />
          <el-option label="Dify" value="DIFY" />
        </el-select>
        <el-input
          v-model="searchText"
          style="width: 260px"
          :placeholder="t('agent.filter.searchPlaceholder')"
          clearable
          @clear="handleCurrentChange(1)"
          @input="handleCurrentChange(1)"
        >
          <template #suffix>
            <el-icon class="el-input__icon">
              <SearchLine v-show="searchText.length === 0" />
            </el-icon>
          </template>
        </el-input>
      </div>
    </div>

    <!-- Card Grid -->
    <div
      v-loading="loading"
      :element-loading-svg="svg"
      element-loading-svg-view-box="-10, -10, 50, 50"
    >
      <el-empty
        v-if="filteredDefinitions.length === 0"
        :description="searchText ? t('agent.notFound', { name: searchText }) : t('agent.empty')"
      />

      <template v-if="filteredDefinitions.length > 0">
        <el-row :gutter="12">
          <el-col
            v-for="(def, index) in pagedList"
            :key="def.id || index"
            :xs="24"
            :sm="12"
            :md="6"
            :lg="6"
          >
            <ListCard
              :definition="def"
              @edit="onEditDefinition"
              @publish="handlePublishVersion"
              @delete="handleDelete"
            />
          </el-col>
        </el-row>

        <el-pagination
          v-model:current-page="pagination.currentPage"
          class="float-right mt-1"
          :page-size="pagination.pageSize"
          :total="filteredDefinitions.length"
          :page-sizes="[12, 24, 36, 48]"
          :background="true"
          layout="total, sizes, prev, pager, next, jumper"
          @size-change="handleSizeChange"
          @current-change="handleCurrentChange"
        />
      </template>
    </div>
  </div>
</template>
