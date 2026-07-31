<script setup lang="ts">
import { useI18n } from "vue-i18n";
import FeaturePlaceholder from "@/components/FeaturePlaceholder/index.vue";

defineOptions({ name: "InstanceMemoryTab" });

const { t } = useI18n();
</script>

<template>
  <div class="memory-tab">
    <!-- 后端无 memories endpoint（agent_runtime.py 注明 memory 本期不实现）→ 占位 -->
    <FeaturePlaceholder
      :title="t('agent.memory.placeholder.title')"
      :description="t('agent.memory.placeholder.desc')"
    />
  </div>
</template>

<style scoped>
.memory-tab {
  margin-bottom: 20px;
}
</style>
