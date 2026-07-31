<script setup lang="ts">
import DocsLink from "@/components/DocsLink/index.vue";
import { ref, onMounted } from "vue";
import { useRouter } from "vue-router";
import { useI18n } from "vue-i18n";
import { getPublicArticlesApi, type ArticleListItem } from "@/api/manager/community";
import { message } from "@/utils/message";
import SearchLine from "~icons/ri/search-line";

defineOptions({ name: "CommunityList" });

const router = useRouter();
const { t } = useI18n();

const loading = ref(false);
const items = ref<ArticleListItem[]>([]);
const total = ref(0);
const page = ref(1);
const pageSize = ref(12);
const searchText = ref("");

async function fetchList() {
  loading.value = true;
  try {
    const res = await getPublicArticlesApi({
      page: page.value,
      page_size: pageSize.value,
      q: searchText.value || undefined
    });
    items.value = res.items || [];
    total.value = res.total || 0;
  } catch (e: any) {
    message(t("community.msg.loadFailed"), { type: "error" });
  } finally {
    loading.value = false;
  }
}

function onSearchInput() {
  if (page.value !== 1) page.value = 1;
  fetchList();
}

function onPageChange(p: number) {
  page.value = p;
  fetchList();
}

function goToDetail(item: ArticleListItem) {
  router.push(`/community/${item.slug}`);
}

function goToCreate() {
  router.push("/community/create");
}

function formatDate(s: string | null): string {
  if (!s) return "";
  const d = new Date(s);
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;
}

onMounted(() => {
  fetchList();
});
</script>

<template>
  <div class="main">
    <DocsLink to="community.html#write" />
    <!-- 顶部：标题 + 操作 -->
    <div class="w-full flex flex-wrap items-center justify-between mb-4 gap-3">
      <div class="flex items-center gap-3 flex-wrap">
        <h2 class="text-xl font-semibold m-0">{{ t("community.listTitle") }}</h2>
      </div>
      <div class="flex items-center gap-3 flex-wrap">
        <el-input
          v-model="searchText"
          :placeholder="t('community.listSearchPlaceholder')"
          clearable
          style="width: 260px"
          @input="onSearchInput"
          @clear="onSearchInput"
        >
          <template #suffix>
            <el-icon v-show="searchText.length === 0"><SearchLine /></el-icon>
          </template>
        </el-input>
        <el-button type="primary" @click="goToCreate">
          {{ t("community.btnCreate") }}
        </el-button>
      </div>
    </div>

    <!-- 文章卡片网格 -->
    <div v-loading="loading" class="w-full">
      <el-row v-if="items.length" :gutter="16">
        <el-col
          v-for="item in items"
          :key="item.id"
          :xs="24"
          :sm="12"
          :md="8"
          :lg="6"
          class="mb-4"
        >
          <el-card
            shadow="hover"
            class="article-card cursor-pointer h-full"
            @click="goToDetail(item)"
          >
            <div class="article-title ellipsis-2">{{ item.title }}</div>
            <div class="article-excerpt ellipsis-3">
              {{ item.excerpt || "" }}
            </div>
            <div class="article-meta">
              <span class="meta-author">{{ item.author_name || "—" }}</span>
              <span class="meta-dot">·</span>
              <span>{{ formatDate(item.published_at) }}</span>
              <span class="meta-dot">·</span>
              <span>{{ t("community.viewCount") }} {{ item.view_count }}</span>
            </div>
          </el-card>
        </el-col>
      </el-row>
      <el-empty v-else :description="t('community.listEmpty')" />
    </div>

    <!-- 分页 -->
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

<style scoped>
.article-card {
  display: flex;
  flex-direction: column;
  transition: transform 0.15s ease;
}
.article-card:hover {
  transform: translateY(-2px);
}
.article-title {
  font-size: 16px;
  font-weight: 600;
  line-height: 1.4;
  margin-bottom: 8px;
  color: var(--el-text-color-primary);
  height: 44px;
}
.article-excerpt {
  font-size: 13px;
  color: var(--el-text-color-secondary);
  line-height: 1.5;
  margin-bottom: 12px;
  height: 58px;
}
.article-meta {
  font-size: 12px;
  color: var(--el-text-color-secondary);
  display: flex;
  align-items: center;
  gap: 6px;
  flex-wrap: wrap;
}
.meta-author {
  font-weight: 500;
}
.meta-dot {
  opacity: 0.5;
}
.ellipsis-2 {
  display: -webkit-box;
  -webkit-line-clamp: 2;
  -webkit-box-orient: vertical;
  overflow: hidden;
}
.ellipsis-3 {
  display: -webkit-box;
  -webkit-line-clamp: 3;
  -webkit-box-orient: vertical;
  overflow: hidden;
}
</style>
