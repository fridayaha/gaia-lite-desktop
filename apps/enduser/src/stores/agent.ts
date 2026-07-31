import { defineStore } from "pinia"
import { ref, computed } from "vue"
import {
  getAccessibleAgents,
  getAgentStatus,
  deployAgent,
  type AccessibleAgent,
  type AgentDeploymentStatus,
  type DeployProgressEvent,
} from "@/api/endpoints"

const sleep = (ms: number) => new Promise((r) => setTimeout(r, ms))

/** 据 (status, pod_phase) 推导粗粒度进度快照（与 DeployProgress 步骤 key 对齐） */
function deriveProgress(s: AgentDeploymentStatus): DeployProgressEvent {
  if (s.status === "RUNNING") {
    return { step: "engine_ready", message: "部署完成", percentage: 100, engine_url: s.engine_url || undefined }
  }
  if (s.status === "FAILED") {
    return { step: "error", message: s.error_message || "部署失败", percentage: 0 }
  }
  // DEPLOYING：按 pod 出现 / Running 逐级推进
  if (s.pod_phase === "Running") {
    return { step: "waiting_ready", message: "等待引擎就绪...", percentage: 75 }
  }
  if (s.pod_name || s.pod_phase === "Pending") {
    return { step: "creating_pod", message: "沙箱环境申请中...", percentage: 35 }
  }
  return { step: "starting", message: "准备部署", percentage: 15 }
}

export const useAgentStore = defineStore("agent", () => {
  const accessibleAgents = ref<AccessibleAgent[]>([])
  const currentAgentId = ref<string | null>(null)
  const deploymentStatus = ref<AgentDeploymentStatus | null>(null)
  const deployProgress = ref<DeployProgressEvent[]>([])
  const isDeploying = ref(false)
  const error = ref<string | null>(null)

  const currentAgent = computed(() =>
    accessibleAgents.value.find((a) => a.id === currentAgentId.value) || null
  )

  async function loadAccessibleAgents() {
    accessibleAgents.value = await getAccessibleAgents()
  }

  async function checkStatus(agentId: string): Promise<AgentDeploymentStatus> {
    currentAgentId.value = agentId
    const status = await getAgentStatus(agentId)
    deploymentStatus.value = status
    return status
  }

  /**
   * 触发部署并轮询 deployment-status 直至 RUNNING/FAILED/超时。
   * deploy POST 异步返回 DEPLOYING，主体在 manager 后台任务跑；
   * 每 2s 轮询一次，最多 3min。deployProgress 推送推导出的进度快照供 UI 渲染。
   */
  async function startDeploy(agentId: string): Promise<boolean> {
    currentAgentId.value = agentId
    isDeploying.value = true
    error.value = null
    deployProgress.value = []

    try {
      await deployAgent(agentId)
    } catch (err: any) {
      error.value = err?.message || "部署请求失败"
      isDeploying.value = false
      return false
    }

    await sleep(1000) // 等后台任务置 DEPLOYING
    const deadline = Date.now() + 180_000
    while (Date.now() < deadline) {
      let status: AgentDeploymentStatus
      try {
        status = await getAgentStatus(agentId)
      } catch {
        await sleep(2000)
        continue
      }
      deploymentStatus.value = status
      deployProgress.value.push(deriveProgress(status))

      if (status.status === "RUNNING") {
        isDeploying.value = false
        return true
      }
      if (status.status === "FAILED") {
        error.value = status.error_message || "部署失败"
        isDeploying.value = false
        return false
      }
      await sleep(2000)
    }
    error.value = "部署超时，请稍后重试"
    isDeploying.value = false
    return false
  }

  function setDeploymentStatus(status: AgentDeploymentStatus) {
    deploymentStatus.value = status
  }

  function reset() {
    currentAgentId.value = null
    deploymentStatus.value = null
    deployProgress.value = []
    isDeploying.value = false
    error.value = null
  }

  return {
    accessibleAgents,
    currentAgentId,
    deploymentStatus,
    deployProgress,
    isDeploying,
    error,
    currentAgent,
    loadAccessibleAgents,
    checkStatus,
    startDeploy,
    setDeploymentStatus,
    reset,
  }
})
