<script setup lang="ts">
import { computed } from "vue"

const props = defineProps<{ currentPanel: string }>()
const emit = defineEmits<{
  switch: [panel: string]
  back: []
}>()

const tabs = [
  {
    panel: "chat",
    label: "对话",
    svg: '<path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z"/>',
  },
  {
    panel: "tasks",
    label: "定时",
    svg: '<circle cx="12" cy="12" r="10"/><polyline points="12 6 12 12 16 14"/>',
  },
  {
    panel: "kanban",
    label: "看板",
    svg: '<rect x="3" y="3" width="7" height="9" rx="1"/><rect x="14" y="3" width="7" height="5" rx="1"/><rect x="14" y="12" width="7" height="9" rx="1"/>',
  },
  {
    panel: "skills",
    label: "技能",
    svg: '<polygon points="13 2 3 14 12 14 11 22 21 10 12 10 13 2"/>',
  },
]

function onClick(panel: string) {
  emit("switch", panel)
}
function onBack() {
  emit("back")
}
</script>

<template>
  <nav class="bottom-tabbar" aria-label="底部导航">
    <button
      v-for="tab in tabs"
      :key="tab.panel"
      class="bottom-tabbar-tab"
      :class="{ active: props.currentPanel === tab.panel }"
      @click="onClick(tab.panel)"
      :aria-label="tab.label"
    >
      <svg width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round" v-html="tab.svg" />
      <span class="bottom-tabbar-label">{{ tab.label }}</span>
    </button>
    <button class="bottom-tabbar-tab" @click="onBack" aria-label="返回智能体列表">
      <svg width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"><path d="M19 12H5"/><polyline points="12 19 5 12 12 5"/></svg>
      <span class="bottom-tabbar-label">返回</span>
    </button>
  </nav>
</template>
