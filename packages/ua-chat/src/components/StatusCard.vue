<template>
  <div class="msg-usage" v-if="visible">
    <span v-if="model" class="msg-usage-model">{{ model }}</span>
    <span v-if="duration" class="msg-usage-sep"> · </span>
    <span v-if="duration" class="msg-usage-dur">{{ fmtDur(duration) }}</span>
    <span v-if="tokens" class="msg-usage-sep"> · </span>
    <span v-if="tokens" class="msg-usage-tok">{{ fmtNum(tokens) }} tokens</span>
  </div>
</template>

<script setup lang="ts">
defineProps<{
  model?: string
  tokens?: number
  duration?: number
  visible: boolean
}>()

function fmtNum(n: number): string {
  return n.toLocaleString()
}

function fmtDur(s: number): string {
  if (s < 60) return `${s.toFixed(1)}秒`
  const m = Math.floor(s / 60)
  const rs = Math.round(s % 60)
  return `${m}分${rs}秒`
}
</script>
