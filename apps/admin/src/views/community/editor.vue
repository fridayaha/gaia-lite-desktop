<script setup lang="ts">
import { ref, onMounted, computed } from "vue";
import { useRoute, useRouter } from "vue-router";
import { useI18n } from "vue-i18n";
import { useDark } from "@vueuse/core";
import { ElMessageBox } from "element-plus";
import { MdEditor } from "md-editor-v3";
import "md-editor-v3/lib/style.css";
import {
  createArticleApi,
  updateArticleApi,
  submitArticleApi,
  getPublicArticleApi,
  type ArticleResponse
} from "@/api/manager/community";
import { message } from "@/utils/message";
import ArrowLeftLine from "~icons/ri/arrow-left-line";

defineOptions({ name: "CommunityEditor" });

const route = useRoute();
const router = useRouter();
const { t, locale } = useI18n();
const isDark = useDark();

const editorLanguage = computed<"zh-CN" | "en-US">(() =>
  locale.value.startsWith("zh") ? "zh-CN" : "en-US"
);

const isEdit = computed(() => !!route.params.slug);
const loading = ref(false);
const saving = ref(false);
const submitting = ref(false);

const articleId = ref<string>("");
const title = ref("");
const slug = ref("");
const excerpt = ref("");
const content = ref("");

async function loadForEdit(slugOrId: string) {
  loading.value = true;
  try {
    const a: ArticleResponse = await getPublicArticleApi(slugOrId);
    articleId.value = a.id;
    title.value = a.title;
    slug.value = a.slug;
    excerpt.value = a.excerpt || "";
    content.value = a.content;
  } catch (e: any) {
    message(t("community.msg.loadFailed"), { type: "error" });
    router.replace("/community/list");
  } finally {
    loading.value = false;
  }
}

async function saveDraft() {
  if (!title.value.trim()) {
    message(t("community.titlePlaceholder"), { type: "warning" });
    return;
  }
  saving.value = true;
  try {
    if (isEdit.value && articleId.value) {
      await updateArticleApi(articleId.value, {
        title: title.value,
        excerpt: excerpt.value,
        content: content.value
      });
      message(t("community.msg.updateSuccess"), { type: "success" });
    } else {
      const res = await createArticleApi({
        title: title.value,
        slug: slug.value || undefined,
        excerpt: excerpt.value,
        content: content.value
      });
      articleId.value = res.id;
      slug.value = res.slug;
      message(t("community.msg.createSuccess"), { type: "success" });
      router.replace(`/community/edit/${res.slug}`);
    }
  } catch (e: any) {
    message(e?.response?.data?.detail || "Save failed", { type: "error" });
  } finally {
    saving.value = false;
  }
}

async function submitForReview() {
  if (!articleId.value) {
    // 先保存草稿
    await saveDraft();
    if (!articleId.value) return;
  }
  if (!title.value.trim() || !content.value.trim()) {
    message(t("community.titlePlaceholder"), { type: "warning" });
    return;
  }
  try {
    await ElMessageBox.confirm(
      t("community.msg.submitConfirm"),
      t("community.btnSubmit"),
      { type: "info" }
    );
  } catch {
    return;
  }
  submitting.value = true;
  try {
    await submitArticleApi(articleId.value);
    message(t("community.msg.submitSuccess"), { type: "success" });
    router.replace(`/community/edit/${slug.value}`);
  } catch (e: any) {
    message(e?.response?.data?.detail || "Submit failed", { type: "error" });
  } finally {
    submitting.value = false;
  }
}

function goBack() {
  router.push("/community/list");
}

onMounted(() => {
  if (isEdit.value && route.params.slug) {
    loadForEdit(String(route.params.slug));
  }
});
</script>

<template>
  <div class="main" v-loading="loading">
    <div class="editor-header mb-4">
      <el-button text @click="goBack">
        <ArrowLeftLine width="16" height="16" class="mr-1" />
        {{ t("community.listTitle") }}
      </el-button>
      <span class="header-title">
        {{ isEdit ? t("community.editArticle") : t("community.createArticle") }}
      </span>
    </div>

    <div class="editor-form mb-4">
      <el-row :gutter="16">
        <el-col :xs="24" :md="18">
          <el-form label-position="top">
            <el-form-item :label="t('community.titleLabel')">
              <el-input
                v-model="title"
                :placeholder="t('community.titlePlaceholder')"
                maxlength="200"
                show-word-limit
              />
            </el-form-item>
          </el-form>
        </el-col>
        <el-col :xs="24" :md="6">
          <el-form label-position="top">
            <el-form-item :label="t('community.slugLabel')">
              <el-input
                v-model="slug"
                :placeholder="t('community.slugPlaceholder')"
                :disabled="isEdit"
              />
            </el-form-item>
          </el-form>
        </el-col>
      </el-row>
      <el-form label-position="top">
        <el-form-item :label="t('community.excerptLabel')">
          <el-input
            v-model="excerpt"
            type="textarea"
            :rows="2"
            :placeholder="t('community.excerptPlaceholder')"
            maxlength="500"
            show-word-limit
          />
        </el-form-item>
      </el-form>
    </div>

    <div class="editor-content">
      <div class="content-label">{{ t("community.contentLabel") }}</div>
      <MdEditor
        v-model="content"
        :theme="isDark ? 'dark' : 'light'"
        :language="editorLanguage"
        :preview="true"
        height="520px"
        :toolbars-exclude="['github', 'save', 'pageFullscreen', 'catalog']"
        style="border-radius: 8px; overflow: hidden"
      />
    </div>

    <div class="editor-actions mt-4">
      <el-button type="primary" :loading="saving" @click="saveDraft">
        {{ t("community.btnSaveDraft") }}
      </el-button>
      <el-button type="success" :loading="submitting" @click="submitForReview">
        {{ t("community.btnSubmit") }}
      </el-button>
    </div>
  </div>
</template>

<style scoped>
.editor-header {
  display: flex;
  align-items: center;
  gap: 12px;
}
.header-title {
  font-size: 18px;
  font-weight: 600;
  color: var(--el-text-color-primary);
}
.content-label {
  font-size: 14px;
  font-weight: 500;
  margin-bottom: 8px;
  color: var(--el-text-color-primary);
}
.editor-actions {
  display: flex;
  gap: 12px;
  justify-content: flex-end;
}
</style>
