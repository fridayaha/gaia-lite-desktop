<template>
  <div class="w-full max-w-md mx-auto text-center">
    <div class="mb-8">
      <!-- 旋转动画 -->
      <div v-if="!error" class="w-16 h-16 border-4 border-blue-100 border-t-blue-500 rounded-full animate-spin mx-auto mb-4"></div>
      <div v-else class="w-16 h-16 bg-red-50 rounded-full flex items-center justify-center mx-auto mb-4">
        <span class="text-2xl">⚠️</span>
      </div>

      <h3 class="text-lg font-medium text-gray-700 mb-2">
        {{ error ? "部署失败" : "正在部署智能体引擎..." }}
      </h3>

      <!-- 进度条 -->
      <div class="w-full bg-gray-100 rounded-full h-2 mb-3">
        <div
          class="deploy-progress-bar h-full rounded-full transition-all duration-500"
          :class="error ? 'bg-red-400' : 'bg-blue-500'"
          :style="{ width: percentage + '%' }"
        ></div>
      </div>

      <p class="text-sm text-gray-400">{{ message }}</p>
    </div>

    <!-- 步骤列表（4 档，按 percentage 阈值点亮） -->
    <ul class="space-y-3 text-left">
      <li
        v-for="step in steps"
        :key="step.key"
        class="flex items-center gap-3 text-sm"
        :class="stepClass(step)"
      >
        <span class="step-icon text-lg w-5 text-center">
          {{ stepIcon(step) }}
        </span>
        <span>{{ step.label }}</span>
      </li>
    </ul>

    <!-- 错误 + 重试 -->
    <div v-if="error" class="mt-8">
      <p class="text-sm text-red-500 bg-red-50 rounded-lg px-4 py-2 mb-4">{{ error }}</p>
      <button
        @click="$emit('retry')"
        class="px-6 py-2 bg-blue-600 text-white rounded-lg text-sm hover:bg-blue-700 transition-colors"
      >
        重试
      </button>
    </div>
  </div>
</template>

<script setup lang="ts">
import { computed } from "vue"

const props = defineProps<{
  percentage: number
  message: string
  error: string | null
}>()

defineEmits<{ retry: [] }>()

/** 4 档步骤 + 完成阈值（与 store.deriveProgress 的百分比档位对齐） */
const steps = [
  { key: "starting", label: "准备部署", threshold: 15 },
  { key: "creating_pod", label: "沙箱环境申请中...", threshold: 35 },
  { key: "waiting_ready", label: "等待引擎就绪...", threshold: 75 },
  { key: "engine_ready", label: "部署完成", threshold: 100 },
]

type Step = (typeof steps)[number]

const isDone = (s: Step) => props.percentage >= s.threshold
const activeStep = computed(() => steps.find((s) => !isDone(s)) || null)

function stepClass(s: Step) {
  if (isDone(s)) return "step-done text-green-600"
  if (s === activeStep.value) return "step-active text-blue-600"
  if (props.error) return "text-red-400"
  return "step-pending text-gray-300"
}

function stepIcon(s: Step) {
  if (isDone(s)) return "✓"
  if (s === activeStep.value) return "◉"
  return "○"
}
</script>
