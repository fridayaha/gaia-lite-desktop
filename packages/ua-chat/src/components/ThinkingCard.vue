<template>
  <div class="thinking-card-row" v-if="visible">
    <div class="thinking-card" :class="{ open: expanded }">
      <div class="thinking-card-header" @click="expanded = !expanded">
        <span class="thinking-card-icon">
          <span class="tool-card-running-dot" v-if="status === 'thinking'"></span>
          <LucideIcon v-else name="brain" :size="13" :stroke-width="2" />
        </span>
        <span>{{ status === 'thinking' ? '思考中' : '已思考' }}</span>
        <span class="thinking-card-toggle"><LucideIcon name="chevron-right" :size="12" :stroke-width="2" /></span>
        <button
          v-if="text"
          class="thinking-copy-btn"
          :title="copied ? '已复制' : '复制思考过程'"
          @click.stop="copyText"
        >
          <LucideIcon v-if="copied" name="check" :size="13" :stroke-width="2" />
          <LucideIcon v-else name="copy" :size="13" :stroke-width="2" />
        </button>
      </div>
      <div class="thinking-card-body" v-if="text">
        <pre v-text="text"></pre>
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
import { ref } from "vue"
import LucideIcon from "../icons/LucideIcon.vue"
import { copyTextToClipboard } from "../clipboard"

const props = withDefaults(defineProps<{
  text?: string
  status?: 'thinking' | 'done' | 'pending'
  visible?: boolean
}>(), {
  text: '',
  status: 'pending',
  visible: true,
})

// 对齐 hermes-webui：流式思考时展开，完成(done)后默认折叠，用户可点 chevron 回看
const expanded = ref(props.status === 'thinking')
const copied = ref(false)
let _copiedTimer: ReturnType<typeof setTimeout> | null = null

async function copyText() {
  if (!props.text) return
  const ok = await copyTextToClipboard(props.text)
  if (!ok) return
  copied.value = true
  if (_copiedTimer) clearTimeout(_copiedTimer)
  _copiedTimer = setTimeout(() => { copied.value = false }, 1500)
}
</script>
