<script setup lang="ts">
import { watch } from "vue";
import { useImageVerify } from "./hooks";

defineOptions({
  name: "ReImageVerify"
});

interface Props {
  /** @deprecated 旧版前端 canvas 模式残留 prop，新版走后端取图，忽略此值。
   * 保留是为兼容 login/index.vue 中 v-if="false" 的引用能通过 typecheck。 */
  code?: string;
}

interface Emits {
  (e: "update:code", code: string): void;
  (e: "update:captchaId", id: string): void;
}

const props = withDefaults(defineProps<Props>(), {
  code: ""
});

const emit = defineEmits<Emits>();

const { imgCode, captchaId, refresh, loading } = useImageVerify();

watch(captchaId, newValue => {
  emit("update:captchaId", newValue);
});

// 兼容旧 API：父组件 v-model:code 仍能拿到值（实际无用，仅保 typecheck 通过）
watch(imgCode, () => {
  emit("update:code", captchaId.value);
});

defineExpose({ refresh });
</script>

<template>
  <div
    class="cursor-pointer select-none"
    style="width: 120px; height: 40px"
    :title="loading ? '加载中...' : '点击刷新'"
    @click="refresh"
  >
    <img
      v-if="imgCode"
      :src="imgCode"
      width="120"
      height="40"
      style="display: block"
      alt="图形验证码"
    />
    <div
      v-else
      class="flex items-center justify-center bg-gray-100 text-gray-400"
      style="width: 120px; height: 40px; font-size: 12px"
    >
      {{ loading ? "加载中..." : "点击获取" }}
    </div>
  </div>
</template>
