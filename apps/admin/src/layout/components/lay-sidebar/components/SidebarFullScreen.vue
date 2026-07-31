<script setup lang="ts">
import { ref, watch } from "vue";
import { useTranslationLang } from "@/layout/hooks/useTranslationLang";
import { useNav } from "@/layout/hooks/useNav";

const screenIcon = ref();
const { toggle, isFullscreen, Fullscreen, ExitFullscreen } = useNav();
const { t } = useTranslationLang();

isFullscreen.value = !!(
  document.fullscreenElement ||
  document.webkitFullscreenElement ||
  document.mozFullScreenElement ||
  document.msFullscreenElement
);

watch(
  isFullscreen,
  full => {
    screenIcon.value = full ? ExitFullscreen : Fullscreen;
  },
  {
    immediate: true
  }
);
</script>

<template>
  <span
    class="fullscreen-icon navbar-bg-hover hover:[&>svg]:animate-scale-bounce"
    :title="t('tableBar.pureFullScreen')"
    @click="toggle"
  >
    <IconifyIconOffline :icon="screenIcon" />
  </span>
</template>
