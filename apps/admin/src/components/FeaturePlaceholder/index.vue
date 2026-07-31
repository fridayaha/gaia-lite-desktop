<script setup lang="ts">
import ToolsLine from "~icons/ri/tools-line";

defineOptions({ name: "FeaturePlaceholder" });
defineProps<{
  title: string;
  description?: string;
}>();
</script>

<template>
  <el-empty class="feature-placeholder">
    <template #image>
      <el-icon :size="52" class="fp-icon">
        <slot name="icon"><ToolsLine /></slot>
      </el-icon>
    </template>
    <template #description>
      <p class="fp-title">{{ title }}</p>
      <p v-if="description" class="fp-desc">{{ description }}</p>
    </template>
    <div v-if="$slots.default" class="fp-extra">
      <slot />
    </div>
  </el-empty>
</template>

<style scoped>
.feature-placeholder {
  padding: 48px 16px;
}

.fp-icon {
  color: var(--el-text-color-placeholder);
}

.fp-title {
  font-size: 15px;
  font-weight: 600;
  color: var(--el-text-color-primary);
  margin: 0 0 6px;
}

.fp-desc {
  font-size: 13px;
  line-height: 1.7;
  margin: 0;
  color: var(--el-text-color-secondary);
  max-width: 460px;
}

.fp-extra {
  margin-top: 12px;
}
</style>
