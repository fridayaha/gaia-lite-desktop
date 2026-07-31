<template>
  <svg
    :width="size"
    :height="size"
    viewBox="0 0 24 24"
    fill="none"
    stroke="currentColor"
    :stroke-width="strokeWidth"
    stroke-linecap="round"
    stroke-linejoin="round"
    aria-hidden="true"
    v-html="LUCIDE_PATHS[name] || LUCIDE_PATHS['file-text']"
  />
</template>

<script setup lang="ts">
import { LUCIDE_PATHS } from "./lucide"

withDefaults(defineProps<{
  name: string
  size?: number | string
  strokeWidth?: number | string
}>(), {
  size: 14,
  strokeWidth: 2,
})
</script>
