<template>
  <div class="approval-card" :class="{ visible }" role="alertdialog" aria-labelledby="approvalHeading" aria-describedby="approvalDesc">
    <div class="approval-inner">
      <div class="approval-header">
        <LucideIcon name="alert-triangle" :size="14" :stroke-width="2" />
        <span id="approvalHeading">需要确认</span>
      </div>
      <div class="approval-desc" id="approvalDesc">{{ description || '智能体请求执行一个需要人工确认的操作。' }}</div>
      <pre class="approval-cmd" v-if="command">{{ command }}</pre>
      <div class="approval-btns" v-if="status === 'pending'">
        <button
          v-for="c in choices"
          :key="c"
          class="approval-btn"
          :class="c"
          :disabled="submitting"
          @click="$emit('respond', c)"
        >
          <span class="approval-btn-icon"><LucideIcon :name="iconOf(c)" :size="14" :stroke-width="c === 'once' || c === 'deny' ? 2.5 : 2" /></span>
          <span class="approval-btn-label">{{ labelOf(c) }}</span>
          <kbd class="approval-kbd" v-if="keyOf(c)">{{ keyOf(c) }}</kbd>
        </button>
      </div>
      <div v-else class="approval-responded">
        <span v-if="status === 'responded' && choice">已响应：{{ labelOf(choice) }}</span>
        <span v-else>超时</span>
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
import { onMounted, onBeforeUnmount, ref } from "vue"
import LucideIcon from "../icons/LucideIcon.vue"

type ApprovalChoice = "once" | "session" | "always" | "deny"

const props = defineProps<{
  command: string
  description: string
  choices: ApprovalChoice[]
  status: "pending" | "responded"
  choice?: ApprovalChoice
  submitting?: boolean
}>()

const emit = defineEmits<{
  respond: [choice: ApprovalChoice]
}>()

// 挂载后下一帧再加 .visible，触发 translateY+opacity 滑入动画
const visible = ref(false)
onMounted(() => {
  requestAnimationFrame(() => (visible.value = true))
  document.addEventListener("keydown", onKeydown)
})
onBeforeUnmount(() => {
  document.removeEventListener("keydown", onKeydown)
})

function onKeydown(e: KeyboardEvent) {
  if (props.status !== "pending" || props.submitting) return
  if (e.key === "Enter") {
    e.preventDefault()
    if (props.choices.includes("once")) emit("respond", "once")
  } else if (e.key === "d" || e.key === "D") {
    e.preventDefault()
    if (props.choices.includes("deny")) emit("respond", "deny")
  }
}

function labelOf(c: ApprovalChoice): string {
  switch (c) {
    case "once": return "仅本次"
    case "session": return "本会话"
    case "always": return "永久允许"
    case "deny": return "拒绝"
  }
}

function iconOf(c: ApprovalChoice): string {
  switch (c) {
    case "once": return "check"
    case "session": return "lock"
    case "always": return "star"
    case "deny": return "x"
  }
}

function keyOf(c: ApprovalChoice): string {
  switch (c) {
    case "once": return "↵"
    case "session": return ""
    case "always": return ""
    case "deny": return "D"
  }
}
</script>
