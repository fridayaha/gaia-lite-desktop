<script setup lang="ts">
/**
 * SkillStudioLogo — Skill Studio 对话区空态 logo。
 *
 * 沿用 skill-studio 紫色系（--ss-accent / #6d5efc），造型取「代码括号 + 火花」
 * 意象，表达「技能开发」。尺寸默认 64，可通过 style 覆盖（font-size / width）。
 */
defineOptions({ name: "SkillStudioLogo" });
</script>

<template>
  <svg
    xmlns="http://www.w3.org/2000/svg"
    viewBox="0 0 64 64"
    width="64"
    height="64"
    role="img"
    aria-label="Skill Studio"
  >
    <!-- 圆角底板 -->
    <rect x="6" y="6" width="52" height="52" rx="14" fill="var(--ss-accent, #6d5efc)" opacity="0.12" />
    <!-- 代码括号 < > -->
    <path
      d="M26 22 L18 32 L26 42"
      fill="none"
      stroke="var(--ss-accent, #6d5efc)"
      stroke-width="3.5"
      stroke-linecap="round"
      stroke-linejoin="round"
    />
    <path
      d="M38 22 L46 32 L38 42"
      fill="none"
      stroke="var(--ss-accent, #6d5efc)"
      stroke-width="3.5"
      stroke-linecap="round"
      stroke-linejoin="round"
    />
    <!-- 中间火花（技能闪光） -->
    <path
      d="M32 24 L34 30 L40 32 L34 34 L32 40 L30 34 L24 32 L30 30 Z"
      fill="var(--ss-accent, #6d5efc)"
    />
  </svg>
</template>

<style scoped>
svg {
  display: block;
}
</style>
