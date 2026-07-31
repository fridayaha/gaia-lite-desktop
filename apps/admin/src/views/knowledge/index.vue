<script setup lang="ts">
import DocsLink from "@/components/DocsLink/index.vue";
import { useI18n } from "vue-i18n";
import FeaturePlaceholder from "@/components/FeaturePlaceholder/index.vue";

defineOptions({ name: "KnowledgeIndex" });
const { t } = useI18n();
</script>

<template>
  <div class="main">
    <DocsLink to="knowledge.html" />
    <FeaturePlaceholder
      :title="t('knowledge.placeholder.title')"
      :description="t('knowledge.placeholder.desc')"
    />
  </div>
</template>
