<template>
  <div class="down-feedback-mask" @click.self="onCancel">
    <div class="down-feedback-card" role="dialog" aria-modal="true" aria-labelledby="downFeedbackTitle">
      <div class="down-feedback-header">
        <span class="down-feedback-icon">
          <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M7 8v12a2 2 0 0 0 2 2h6a2 2 0 0 0 2-2V8"/><path d="M18.5 4H5.5a2 2 0 0 0-2 2v2a2 2 0 0 0 2 2h13a2 2 0 0 0 2-2V6a2 2 0 0 0-2-2"/><path d="M19 14V8"/></svg>
        </span>
        <span id="downFeedbackTitle" class="down-feedback-title">反馈问题</span>
      </div>
      <p class="down-feedback-desc">请选择问题类型，帮助我们改进回复质量</p>
      <div class="down-feedback-reasons" role="radiogroup">
        <label
          v-for="opt in reasons"
          :key="opt.value"
          class="down-feedback-reason"
          :class="{ active: selected === opt.value }"
        >
          <input type="radio" name="down-reason" :value="opt.value" v-model="selected" />
          <span class="down-feedback-reason-dot"></span>
          <span class="down-feedback-reason-label">{{ opt.label }}</span>
        </label>
      </div>
      <textarea
        class="down-feedback-comment"
        v-model="comment"
        placeholder="补充说明（可选）"
        rows="3"
        maxlength="500"
      ></textarea>
      <div class="down-feedback-actions">
        <button class="down-feedback-btn cancel" @click="onCancel">取消</button>
        <button
          class="down-feedback-btn submit"
          :disabled="!selected || submitting"
          @click="onSubmit"
        >
          {{ submitting ? '提交中…' : '提交反馈' }}
        </button>
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
import { ref } from "vue"

const props = defineProps<{
  message: any
}>()

const emit = defineEmits<{
  submit: [reason: string, comment: string | null]
  cancel: []
}>()

const reasons = [
  { value: "inaccurate", label: "不准确" },
  { value: "harmful", label: "有害或不当" },
  { value: "off_topic", label: "跑题未解决" },
  { value: "other", label: "其他" },
] as const

const selected = ref<string | null>(null)
const comment = ref("")
const submitting = ref(false)

function onSubmit() {
  if (!selected.value || submitting.value) return
  submitting.value = true
  emit("submit", selected.value, comment.value.trim() || null)
}
function onCancel() {
  emit("cancel")
}
</script>
