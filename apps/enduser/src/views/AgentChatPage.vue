<template>
  <!-- 部署阶段 -->
  <div v-if="showDeployPhase" class="chat-container">
    <div class="flex items-center gap-3 px-6 pt-6 pb-2">
      <router-link to="/agents" class="text-gray-400 hover:text-gray-600 text-sm">← 返回</router-link>
      <h1 class="text-lg font-semibold text-gray-800">{{ store.currentAgent?.name }}</h1>
      <span class="text-xs px-2 py-0.5 rounded-full" :class="statusBadgeClass">{{ statusLabel }}</span>
    </div>
    <div class="flex items-center justify-center h-[calc(100vh-180px)] px-6">
      <DeployProgress
        v-if="showDeployPhase"
        :percentage="currentPercentage"
        :message="currentMessage"
        :error="store.error"
        @retry="handleDeploy"
      />
    </div>
  </div>

  <!-- 聊天阶段：直接渲染 ChatPage（无 iframe） -->
  <!-- :key="agentId" 强制切换 agent 时销毁重建整个 chat 子树，确保 useChat/会话/消息按新 agent 重新初始化 -->
  <ChatPage v-else-if="store.deploymentStatus?.status === 'RUNNING'" :key="agentId" :agentId="agentId" :engineType="store.currentAgent?.engine_type" :agentName="store.currentAgent?.name" @engine-unavailable="onEngineUnavailable" />

  <!-- 意外状态 -->
  <div v-else class="chat-container dark-bg flex items-center justify-center">
    <div class="text-center text-gray-400">
      <p>引擎状态: {{ store.deploymentStatus?.status || "未知" }}</p>
      <button @click="handleDeploy" class="mt-3 text-sm text-blue-500 hover:underline">重新部署</button>
    </div>
  </div>
</template>

<script setup lang="ts">
import { ref, computed, onMounted, onUnmounted, watch } from "vue"
import { useRoute } from "vue-router"
import { useAgentStore } from "@/stores/agent"
import { useAuthStore } from "@/stores/auth"
import DeployProgress from "@/components/DeployProgress.vue"
import ChatPage from "@/components/chat/ChatPage.vue"
import HermesLogo from "@/components/icons/HermesLogo.vue"
import OpenClawLogo from "@/components/icons/OpenClawLogo.vue"

const route = useRoute()
const store = useAgentStore()
const auth = useAuthStore()
// 响应式 agentId：路由 param 变化时自动更新，配合 watch 重新初始化
const agentId = computed(() => route.params.id as string)

const currentPercentage = computed(() =>
  store.deployProgress.length ? store.deployProgress[store.deployProgress.length - 1].percentage : 0
)
const currentMessage = computed(() =>
  store.deployProgress.length ? store.deployProgress[store.deployProgress.length - 1].message : ""
)
const showDeployPhase = computed(() =>
  store.isDeploying || ["PENDING", "DEPLOYING", "SUSPENDED", "ARCHIVED"].includes(store.deploymentStatus?.status || "")
)

const statusBadgeClass = computed(() => {
  switch (store.deploymentStatus?.status) {
    case "RUNNING":   return "bg-green-50 text-green-600"
    case "SUSPENDED": return "bg-yellow-50 text-yellow-600"
    case "FAILED":    return "bg-red-50 text-red-600"
    default:          return "bg-gray-50 text-gray-500"
  }
})
const statusLabel = computed(() => store.deploymentStatus?.status || "")

const engineType = computed(() => store.currentAgent?.engine_type)

onMounted(async () => {
  auth.setChatMode(true)
  await initForAgent(agentId.value)
})

// chat 内切换 agent：路由 param 变化时重新初始化（加载状态/触发部署）
watch(agentId, (newId, oldId) => {
  if (newId && newId !== oldId) initForAgent(newId)
})

async function initForAgent(id: string) {
  // 加载智能体列表，确保 currentAgent 有值（engineType 才能正确传递）
  await store.loadAccessibleAgents().catch(() => {})
  try {
    const status = await store.checkStatus(id)
    if (status.status === "RUNNING") {
      // 引擎就绪，直接进入聊天
    } else {
      handleDeploy()
    }
  } catch {
    store.$patch({ error: "无法获取引擎状态" })
  }
}

onUnmounted(() => auth.setChatMode(false))

async function onEngineUnavailable() {
  // Gateway 返回 503 → 重新检查引擎状态并触发重新部署
  try {
    const status = await store.checkStatus(agentId.value)
    if (status.status !== "RUNNING") {
      await handleDeploy()
    }
  } catch {
    store.$patch({ error: "引擎不可用，请稍后重试" })
  }
}

async function handleDeploy() {
  await store.startDeploy(agentId.value)
}
</script>

<style scoped>
.chat-container { width: 100%; }
.chat-container.dark-bg { background: #1a1a2e; min-height: calc(100dvh - 48px); }
</style>
