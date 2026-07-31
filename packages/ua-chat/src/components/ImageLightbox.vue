<template>
  <div class="img-lightbox" role="dialog" :aria-label="currentAlt" @click="close">
    <img :src="currentSrc" :alt="currentAlt" @click.stop />
    <button class="img-lightbox-close" aria-label="关闭" @click.stop="close">×</button>
    <template v-if="images.length > 1">
      <button class="img-lightbox-nav img-lightbox-nav-prev" aria-label="上一张" @click.stop="nav(-1)">‹</button>
      <button class="img-lightbox-nav img-lightbox-nav-next" aria-label="下一张" @click.stop="nav(1)">›</button>
      <div class="img-lightbox-counter">{{ currentIndex + 1 }} / {{ images.length }}</div>
    </template>
  </div>
</template>

<script setup lang="ts">
import { ref, computed, onMounted, onUnmounted } from "vue"

const props = defineProps<{
  images: { src: string; alt: string }[]
  index: number
}>()

const emit = defineEmits<{ close: [] }>()

const currentIndex = ref(props.index)
const currentSrc = computed(() => props.images[currentIndex.value]?.src || "")
const currentAlt = computed(() => props.images[currentIndex.value]?.alt || "Image")

function close() { emit("close") }
function nav(dir: number) {
  const next = currentIndex.value + dir
  if (next >= 0 && next < props.images.length) currentIndex.value = next
}

function onKeydown(e: KeyboardEvent) {
  if (e.key === "Escape") { e.preventDefault(); close() }
  if (e.key === "ArrowLeft") { e.preventDefault(); nav(-1) }
  if (e.key === "ArrowRight") { e.preventDefault(); nav(1) }
}

onMounted(() => document.addEventListener("keydown", onKeydown))
onUnmounted(() => document.removeEventListener("keydown", onKeydown))
</script>
