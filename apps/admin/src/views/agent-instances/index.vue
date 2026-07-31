<script setup lang="ts">
import DocsLink from "@/components/DocsLink/index.vue";
import { useInstance } from "./utils/hook";
import ListCard from "./components/ListCard.vue";
import { useRenderIcon } from "@/components/ReIcon/src/hooks";
import { useI18n } from "vue-i18n";
import AddFill from "~icons/ri/add-circle-line";
import SearchLine from "~icons/ri/search-line";

defineOptions({ name: "AgentInstanceList" });
const { t } = useI18n();

const {
  loading,
  pagedList,
  filteredInstances,
  pagination,
  searchText,
  statusFilter,
  engineFilter,
  publishedCount,
  draftCount,
  offlineCount,
  openDialog,
  handlePublish,
  handleOffline,
  handleDelete,
  handleClone,
  handleSizeChange,
  handleCurrentChange
} = useInstance();

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

function onEditInstance(instance: any) {
  openDialog("edit", instance);
}
</script>

<template>
  <div class="main">
    <DocsLink to="agent-instances.html" />
    <!-- Stats Cards -->
    <el-row :gutter="12" class="mb-1">
      <el-col :span="8">
        <el-card shadow="never" :body-style="{ padding: '6px 12px' }">
          <div class="text-center">
            <div class="text-base font-bold text-primary">{{ publishedCount }}</div>
            <div class="text-xs text-gray-400">{{ t("instance.stats.published") }}</div>
          </div>
        </el-card>
      </el-col>
      <el-col :span="8">
        <el-card shadow="never" :body-style="{ padding: '6px 12px' }">
          <div class="text-center">
            <div class="text-base font-bold text-[#f59e0b]">{{ draftCount }}</div>
            <div class="text-xs text-gray-400">{{ t("instance.stats.draft") }}</div>
          </div>
        </el-card>
      </el-col>
      <el-col :span="8">
        <el-card shadow="never" :body-style="{ padding: '6px 12px' }">
          <div class="text-center">
            <div class="text-base font-bold text-gray-400">{{ offlineCount }}</div>
            <div class="text-xs text-gray-400">{{ t("instance.stats.offline") }}</div>
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
        {{ t("instance.create") }}
      </el-button>
      <div class="flex items-center gap-3 flex-wrap">
        <el-select
          v-model="statusFilter"
          :placeholder="t('instance.filter.allStatus')"
          clearable
          class="w-32!"
          @change="handleCurrentChange(1)"
        >
          <el-option :label="t('instance.filter.allStatus')" value="" />
          <el-option :label="t('common.status.draft')" value="DRAFT" />
          <el-option :label="t('instance.stats.published')" value="PUBLISHED" />
          <el-option :label="t('common.status.offline')" value="OFFLINE" />
        </el-select>
        <el-select
          v-model="engineFilter"
          :placeholder="t('instance.filter.allEngine')"
          clearable
          class="w-32!"
          @change="handleCurrentChange(1)"
        >
          <el-option :label="t('instance.filter.allEngine')" value="" />
          <el-option label="Hermes" value="HERMES" />
          <el-option label="OpenClaw" value="OPENCLAW" />
        </el-select>
        <el-input
          v-model="searchText"
          style="width: 260px"
          :placeholder="t('instance.filter.searchPlaceholder')"
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
        v-if="filteredInstances.length === 0"
        :description="
          searchText ? t('instance.notFound', { name: searchText }) : t('instance.empty')
        "
      />

      <template v-if="filteredInstances.length > 0">
        <el-row :gutter="12">
          <el-col
            v-for="(instance, index) in pagedList"
            :key="instance.id || index"
            :xs="24"
            :sm="12"
            :md="6"
            :lg="6"
          >
            <ListCard
              :instance="instance"
              @edit="onEditInstance"
              @publish="handlePublish"
              @offline="handleOffline"
              @clone="handleClone"
              @delete="handleDelete"
            />
          </el-col>
        </el-row>

        <el-pagination
          v-model:current-page="pagination.currentPage"
          class="float-right mt-1"
          :page-size="pagination.pageSize"
          :total="filteredInstances.length"
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
