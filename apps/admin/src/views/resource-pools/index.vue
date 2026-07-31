<script setup lang="ts">
import DocsLink from "@/components/DocsLink/index.vue";
import { useI18n } from "vue-i18n";
import { useResourcePool } from "./utils/hook";
import ListCard from "./components/ListCard.vue";
import AddFill from "~icons/ri/add-fill";
import SearchLine from "~icons/ri/search-line";

defineOptions({ name: "ResourcePoolList" });

const { t } = useI18n();

const {
  loading, searchText, pagination,
  pagedList, totalCount, autoRecycleCount, manualCount,
  onSearch,
  handleCurrentChange, handleSizeChange,
  openDialog, handleClone, handleDelete
} = useResourcePool();
</script>

<template>
  <div class="main" v-loading="loading" :element-loading-text="t('engine.loading')">
    <DocsLink to="resource-pools.html" />
    <!-- Stats -->
    <el-row :gutter="12" class="mb-4">
      <el-col :xs="24" :sm="8">
        <el-card shadow="hover" class="stat-card">
          <div class="text-gray-500 text-sm">{{ t("engine.stats.total") }}</div>
          <div class="text-2xl font-bold mt-1">{{ totalCount }}</div>
        </el-card>
      </el-col>
      <el-col :xs="24" :sm="8">
        <el-card shadow="hover" class="stat-card">
          <div class="text-gray-500 text-sm">{{ t("engine.autoRecycle") }}</div>
          <div class="text-2xl font-bold mt-1" style="color: #00a870">{{ autoRecycleCount }}</div>
        </el-card>
      </el-col>
      <el-col :xs="24" :sm="8">
        <el-card shadow="hover" class="stat-card">
          <div class="text-gray-500 text-sm">{{ t("engine.manualManage") }}</div>
          <div class="text-2xl font-bold mt-1" style="color: #386bf5">{{ manualCount }}</div>
        </el-card>
      </el-col>
    </el-row>

    <!-- Toolbar -->
    <div class="w-full flex flex-wrap items-center justify-between mb-4 gap-3">
      <div class="flex items-center gap-3 flex-wrap">
          <el-button type="primary" @click="openDialog()">
            <AddFill width="18" height="18" class="mr-1" />
            {{ t("resourcePool.create") }}
          </el-button>
      </div>
      <div class="flex items-center gap-3 flex-wrap">
        <el-input
          v-model="searchText"
          :placeholder="t('engine.filter.searchPlaceholder')"
          style="width: 260px"
          clearable
          @input="handleCurrentChange(1)"
        >
          <template #suffix>
            <SearchLine v-show="searchText.length === 0" width="18" height="18" class="text-gray-400" />
          </template>
        </el-input>
      </div>
    </div>

    <!-- Card Grid -->
    <el-row :gutter="12" v-if="pagedList.length > 0">
      <el-col
        v-for="pool in pagedList"
        :key="pool.id"
        :xs="24" :sm="12" :md="6" :lg="6"
        class="mb-4"
      >
        <ListCard
          :pool="pool"
          @edit="(pool: any) => openDialog('edit', pool)"
          @clone="handleClone"
          @delete="handleDelete"
        />
      </el-col>
    </el-row>

    <el-empty v-else :description="t('engine.empty')" />

    <!-- Pagination -->
    <div v-if="pagedList.length > 0" class="flex justify-end mt-4">
      <el-pagination
        v-model:current-page="pagination.currentPage"
        v-model:page-size="pagination.pageSize"
        :page-sizes="[12, 24, 36, 48]"
        :total="totalCount"
        layout="total, sizes, prev, pager, next, jumper"
        background
        @size-change="handleSizeChange"
        @current-change="handleCurrentChange"
      />
    </div>
  </div>
</template>

<style scoped>
.stat-card {
  border-radius: 8px;
}
</style>
