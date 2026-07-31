<script setup lang="ts">
import { ref, onMounted, computed } from "vue";
import { useRoute, useRouter } from "vue-router";
import { useI18n } from "vue-i18n";
import { useDark } from "@vueuse/core";
import { MdPreview } from "md-editor-v3";
import "md-editor-v3/lib/preview.css";
import { getPublicArticleApi, type ArticleResponse } from "@/api/manager/community";
import { message } from "@/utils/message";
import ArrowLeftLine from "~icons/ri/arrow-left-line";

defineOptions({ name: "CommunityDetail" });

const route = useRoute();
const router = useRouter();
const { t } = useI18n();
const isDark = useDark();

const loading = ref(false);
const article = ref<ArticleResponse | null>(null);

const slugOrId = computed(() => String(route.params.slug || ""));

async function fetchDetail() {
  loading.value = true;
  try {
    article.value = await getPublicArticleApi(slugOrId.value);
  } catch (e: any) {
    message(t("community.msg.loadFailed"), { type: "error" });
    router.replace("/community/list");
  } finally {
    loading.value = false;
  }
}

function goBack() {
  router.push("/community/list");
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
  if (slugOrId.value) fetchDetail();
});
</script>

<template>
  <div class="main" v-loading="loading">
    <div class="detail-header mb-4">
      <el-button text @click="goBack">
        <ArrowLeftLine width="16" height="16" class="mr-1" />
        {{ t("community.listTitle") }}
      </el-button>
    </div>

    <div v-if="article" class="article-wrap">
      <h1 class="article-h1">{{ article.title }}</h1>
      <div class="article-info">
        <span class="info-item">
          <span class="info-label">{{ t("community.byAuthor") }}</span>
          <span class="info-value">{{ article.author_name || "—" }}</span>
        </span>
        <span class="info-dot">·</span>
        <span class="info-item">
          <span class="info-label">{{ t("community.publishedAt") }}</span>
          <span class="info-value">{{ formatDateTime(article.published_at) }}</span>
        </span>
        <span class="info-dot">·</span>
        <span class="info-item">
          <span class="info-value">{{ t("community.viewCount") }} {{ article.view_count }}</span>
        </span>
      </div>

      <el-divider />

      <MdPreview
        :model-value="article.content"
        :theme="isDark ? 'dark' : 'light'"
        class="article-body"
      />
    </div>
  </div>
</template>

<style scoped>
.detail-header {
  display: flex;
  align-items: center;
}
.article-wrap {
  max-width: 860px;
  margin: 0 auto;
}
.article-h1 {
  font-size: 28px;
  font-weight: 700;
  line-height: 1.3;
  margin: 0 0 16px;
  color: var(--el-text-color-primary);
}
.article-info {
  font-size: 13px;
  color: var(--el-text-color-secondary);
  display: flex;
  align-items: center;
  gap: 8px;
  flex-wrap: wrap;
}
.info-item {
  display: inline-flex;
  align-items: center;
  gap: 4px;
}
.info-label {
  opacity: 0.7;
}
.info-value {
  font-weight: 500;
}
.info-dot {
  opacity: 0.4;
}
.article-body {
  padding: 8px 0 32px;
}
</style>
