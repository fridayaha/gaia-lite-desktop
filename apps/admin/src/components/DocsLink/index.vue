<script setup lang="ts">
import { IconifyIconOffline } from "@/components/ReIcon";
import QuestionLine from "~icons/ri/question-line";

defineOptions({ name: "DocsLink" });
defineProps<{
  /** docs 站点内的相对路径（不含 /docs/ 前缀），如 "monitoring.html#trace" */
  to: string;
}>();

const open = (to: string) => {
  window.open(`/docs/guide/pages/${to}`, "_blank");
};
</script>

<template>
  <span
    class="docs-link-fab select-none"
    title="查看本页操作文档"
    @click="open(to)"
  >
    <IconifyIconOffline :icon="QuestionLine" />
  </span>
</template>

<style scoped>
.docs-link-fab {
  position: fixed;
  top: 98px;
  right: 16px;
  z-index: 1000;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 36px;
  height: 36px;
  border-radius: 18px;
  cursor: pointer;
  color: var(--el-text-color-primary);
  background: var(--el-fill-color-light);
  box-shadow: 0 2px 8px rgba(0, 0, 0, 0.08);
  transition: all 0.2s;
}

.docs-link-fab:hover {
  background: var(--el-color-primary-light-8);
  color: var(--el-color-primary);
  transform: scale(1.05);
}
</style>
