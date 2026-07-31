<template>
  <img v-if="imgSrc" class="msg-media-img" :src="imgSrc" :alt="alt" loading="lazy" @click="$emit('click', $event)" />
  <div v-else class="msg-media-img msg-media-img--loading">
    <svg v-if="error" width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5"><circle cx="12" cy="12" r="10"/><line x1="12" y1="8" x2="12" y2="12"/><line x1="12" y1="16" x2="12.01" y2="16"/></svg>
    <span v-else class="msg-media-loading-dot"></span>
  </div>
</template>

<script setup lang="ts">
import { ref, watch, inject } from "vue";
import { chatContextKey } from "../chatContext";

const props = defineProps<{
  path: string;
  alt?: string;
}>();

defineEmits<{ click: [e: MouseEvent] }>();

const ctx = inject(chatContextKey, {});

const imgSrc = ref<string | null>(null);
const error = ref(false);

async function fetchImage() {
  error.value = false;
  imgSrc.value = null;
  if (!ctx.imageResolver) {
    error.value = true;
    return;
  }
  try {
    const data = await ctx.imageResolver(props.path);
    if (data?.is_image && data.content_b64) {
      const ext = props.path.toLowerCase().match(/\.[^.]+$/)?.[0] || ".png";
      const mime = ext === ".svg" ? "image/svg+xml" : ext === ".webp" ? "image/webp" : `image/${ext.slice(1)}`;
      imgSrc.value = `data:${mime};base64,${data.content_b64}`;
    } else {
      error.value = true;
    }
  } catch {
    error.value = true;
  }
}

watch(() => props.path, fetchImage, { immediate: true });
</script>
