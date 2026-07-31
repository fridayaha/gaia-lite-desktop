/**
 * 聊天核心逻辑 composable
 * 管理会话生命周期、消息发送、SSE 流式接收、工作区、配置文件
 */
import { ref, computed, watch } from "vue"
import { shouldShowSilenceHint } from "@/utils/silenceHint"

interface Session {
  session_id: string
  title: string
  messages: Message[]
  /** 历史消息是否已加载完成。false=尚未加载（点击时应显示 loading 而非欢迎屏）；
   *  true=已加载（即便 messages 为空也算"加载完的真空白"，可显示欢迎屏）。
   *  newSession 创建的会话直接置 true（无历史可加载）。 */
  _loaded?: boolean
  created_at: number
  last_message_at: number | null
  model: string
  /** Dify 集成模式：Dify 侧 conversation_id，首轮回复后由 SSE 捕获。
   *  HERMES 引擎不使用此字段（其会话由 Controller session_store 管理）。 */
  engine_conversation_id?: string
}

interface Attachment {
  name: string
  path: string
  is_image: boolean
  blobUrl?: string
}

interface Message {
  role: "user" | "assistant" | "system"
  content: string
  isError?: boolean
  /** 引擎历史消息的自增 id（hermes v2 等回传）；本地流式刚完成的消息没有 → 用 hash 兜底。
   *  与 Android Message.id 同字段，跨平台 message_ref 一致：mid:{id} 或 hash:{sha256} */
  id?: number
  _turnUsage?: any
  _model?: string
  _turnDuration?: number
  _ts?: number
  provider_details?: string
  _toolCalls?: any[]
  _thinkingText?: string
  _activityEvents?: any[]
  _retryable?: boolean
  _retryKind?: string
  _feedback?: "up" | "down"
  _favorite?: boolean
  /** 该消息所属 run 的 id（用于 feedback API 的 run_id 字段；SSE 完成时落） */
  _runId?: string
  attachments?: Attachment[]
}

import { getAgentModels, listAgentFiles, readAgentFileContent, upsertFeedback, listFeedback, upsertFavorite, deleteFavorite, listSessionFavorites } from "@/api/endpoints"
import { stripAttachmentHint } from "@/utils/attachment"
import { isReasoningEchoOfReply } from "@/utils/reasoningDedup"
import { useAuthStore } from "@/stores/auth"
import { getAccessToken, refreshAccessToken, redirectToLogin } from "@/api/auth"
import { ApiError, api } from "@/api/client"

const GW_BASE = "/api/gateway"

// ── 中断恢复：pending run 持久化（嫁接自 Repo1 resumePendingHermesRuns）──
// chat-completions 流期间记录 run_id（由网关在 run.start 事件下发），
// 流正常结束清除；若页面刷新/关闭中断，下次挂载时对未过期(5min)的 run 自动续接。
const PENDING_RUNS_KEY = "ua_pending_runs"
const PENDING_RUN_TTL_MS = 5 * 60 * 1000

export type ApprovalChoice = "once" | "session" | "always" | "deny"

export interface ApprovalState {
  runId: string
  command: string
  description: string
  choices: ApprovalChoice[]
  status: "pending" | "responded"
  choice?: ApprovalChoice
  submitting?: boolean
}

interface PendingRun {
  run_id: string
  session_id: string
  agent_id: string
  started_at: number
}

function readPendingRuns(): PendingRun[] {
  try {
    const arr = JSON.parse(localStorage.getItem(PENDING_RUNS_KEY) || "[]")
    const valid = (arr as PendingRun[]).filter(r => Date.now() - r.started_at < PENDING_RUN_TTL_MS)
    if (valid.length !== arr.length) localStorage.setItem(PENDING_RUNS_KEY, JSON.stringify(valid))
    return valid
  } catch {
    return []
  }
}

function writePendingRuns(runs: PendingRun[]) {
  localStorage.setItem(PENDING_RUNS_KEY, JSON.stringify(runs))
}

// ── 消息反馈 / 收藏（manager 服务端持久化，对齐 Android MessageFeedbackRepository）──
// message_ref：引擎历史消息有自增 id → "mid:{id}"；本地流式刚完成或引擎无 id → "hash:{sha256(content)[:16]}"
// 跨平台一致：与 Android MessageFeedbackRepository.messageRefOf 同逻辑
const LOCAL_MSG_ID_THRESHOLD = 1_000_000_000_000 // 1e12，本地 user 消息用 Date.now() 占位 ≥ 此值

async function sha256Hex(text: string): Promise<string> {
  const data = new TextEncoder().encode(text)
  const hashBuf = await crypto.subtle.digest("SHA-256", data)
  return Array.from(new Uint8Array(hashBuf)).map(b => b.toString(16).padStart(2, "0")).join("")
}

async function messageRefOf(msg: Message): Promise<string> {
  if (msg.id != null && msg.id < LOCAL_MSG_ID_THRESHOLD) return `mid:${msg.id}`
  return "hash:" + (await sha256Hex(msg.content || "")).slice(0, 16)
}

/** 从 provider/model_name 中提取纯模型名，引擎只认 model_name 部分 */
function bareModel(id: string): string {
  const idx = id.indexOf("/")
  return idx > 0 ? id.slice(idx + 1) : id
}

function getToken(): string | null {
  return getAccessToken()
}

function gwHeaders(agentId: string, opts?: { sessionId?: string | null; engineType?: string }): Record<string, string> {
  const headers: Record<string, string> = {
    "Content-Type": "application/json",
    "X-Agent-ID": agentId,
  }
  const token = getToken()
  if (token) headers["Authorization"] = `Bearer ${token}`
  if (opts?.engineType) headers["X-Engine-Type"] = opts.engineType
  if (opts?.sessionId) headers["X-Session-ID"] = opts.sessionId
  return headers
}

/**
 * 网关裸 fetch 包装：401 时尝试 refresh 续期并重试一次，失败跳登录。
 * SSE 流式请求也走这里 —— 之前裸 fetch 完全绕过鉴权处理，token 过期时只静默报错留页。
 */
async function gwFetch(url: string, init: RequestInit, _retried = false): Promise<Response> {
  const res = await fetch(url, init)
  if (res.status !== 401) return res
  // 401：尝试 refresh 续期
  if (!_retried) {
    const newToken = await refreshAccessToken()
    if (newToken) {
      const headers = new Headers(init.headers)
      headers.set("Authorization", `Bearer ${newToken}`)
      return gwFetch(url, { ...init, headers }, true)
    }
  }
  // refresh 失败 → 跳登录
  redirectToLogin()
  throw new ApiError(401, "Unauthorized")
}

// ── 附件上传（经 manager k8s exec 写入 profile 工作区 uploads/）──
async function uploadFiles(files: File[], sessionId: string, agentId: string, _engineType?: string): Promise<Attachment[]> {
  const result: Attachment[] = []
  for (const file of files) {
    const fd = new FormData()
    fd.append("file", file, file.name)
    const data: any = await api.post(`/manager/agent-instances/${agentId}/files/upload`, fd)
    if (data.error) throw new Error(data.error)
    const isImage = !!data.is_image
    result.push({
      name: data.filename || file.name,
      path: data.path,
      is_image: isImage,
      blobUrl: isImage ? URL.createObjectURL(file) : undefined,
    })
  }
  return result
}

/** 构造发给引擎的单条 message：干净 content + 结构化 attachments（若有）。
 * [Attached files: path] 文本提示由 gateway 在转发引擎前统一合成，前端不再拼。 */
function toEngineMessage(m: any): { role: string; content: string; attachments?: Attachment[] } {
  const msg: { role: string; content: string; attachments?: Attachment[] } = {
    role: m.role,
    content: m.content || "",
  }
  if (m.attachments && m.attachments.length > 0) {
    msg.attachments = m.attachments
  }
  return msg
}

// ── T5：错误分类 + 自动重连策略 ──
const MAX_AUTO_RETRIES = 2 // 共 3 次尝试（首次 + 2 重试）
const RETRY_BACKOFF_MS = 1000 // 基础退避，指数递增（1s, 2s）

/** 把发送/流式错误分类为是否可重试。401 已被 gwFetch 处理，到这里的 401 是 redirect 后残留，不可重试。 */
function classifySendError(e: any, status?: number): { retryable: boolean; kind: string } {
  if (e instanceof ApiError && e.status === 401) return { retryable: false, kind: "auth" }
  const msg = String(e?.message || e || "").toLowerCase()
  if (e instanceof TypeError || msg.includes("failed to fetch") || msg.includes("networkerror") || msg.includes("network error")) {
    return { retryable: true, kind: "network" }
  }
  if (status === 503) return { retryable: true, kind: "engine" }
  if (status === 429 || msg.includes("rate limit")) return { retryable: true, kind: "rate" }
  if (status && status >= 500) return { retryable: true, kind: "server" }
  if (status && status >= 400 && status < 500) return { retryable: false, kind: "client" }
  return { retryable: false, kind: "unknown" }
}

/** 指数退避延迟（attempt 从 0 起：1s, 2s） */
function retryDelay(attempt: number): number {
  return RETRY_BACKOFF_MS * Math.pow(2, attempt)
}

export function useChat(agentId: string, engineType?: string) {
  const sessions = ref<Session[]>([])
  const currentSessionId = ref<string | null>(null)
  const engineAvailable = ref(true)
  const searchQuery = ref("")
  const isStreaming = ref(false)
  const streamingContent = ref("")
  const thinkingText = ref("")
  const thinkingStatus = ref<'thinking' | 'done' | 'pending'>('pending')
  const toolCalls = ref<any[]>([])
  const activityEvents = ref<any[]>([])
  const approvalPending = ref<ApprovalState | null>(null)
  // gateway 静默看门狗帧累计的已等待秒数（null=无静默 / 已被新活动清零）。
  // 由 handleHermesEvent / 非 Hermes 解析路径维护：gateway.silence 帧到达时置为
  // p.elapsed；任何其他 SSE 事件到达即代表有活动，清零。
  const silenceElapsedSeconds = ref<number | null>(null)

  // 对齐 Android SilenceHint：不出独立"已等待 N 秒"横幅，把"等待回复"事件的
  // detail 实时改为"智能体思考中（N秒）"（收起栏预览行/活动 feed 同一行计数）；
  // 工具运行中/审批待响应时不更新（用户已有可见进度）
  function updateWaitingSilenceDetail(elapsed: number | null) {
    if (!shouldShowSilenceHint(elapsed, toolCalls.value, approvalPending.value)) return
    activityEvents.value = activityEvents.value.map((ev: any) =>
      ev.kind === 'waiting' && ev.status === 'waiting'
        ? { ...ev, detail: `智能体思考中（${elapsed}秒）` }
        : ev
    )
  }

  // 首个正文 delta 到达即认为"等待回复"结束（对齐 Android markFirstDeltaIfNeeded）
  watch(streamingContent, (val, prev) => {
    if (val && !prev) {
      activityEvents.value = activityEvents.value.map((ev: any) =>
        ev.kind === 'waiting' && ev.status === 'waiting'
          ? { ...ev, status: 'done', label: '已回复', detail: '' }
          : ev
      )
    }
  })

  // run 收尾：所有仍 waiting 的事件置 done（对齐 Android Completed）；
  // replied=true 时"等待回复"行同步改名"已回复"（无内容收尾如 error/abort 则保留原 label）
  function settleWaitingEvents(replied: boolean) {
    activityEvents.value = activityEvents.value.map((ev: any) =>
      ev.status === 'waiting'
        ? { ...ev, status: 'done', ...(replied && ev.kind === 'waiting' ? { label: '已回复', detail: '' } : {}) }
        : ev
    )
  }
  // 浏览器沙箱接管态：true=用户正接管云桌面（VNC read-write，禁对话框提交）。
  // 与 isStreaming 互斥：run 活跃时禁接管，接管时禁发消息（不会启新 run）。
  const browserTakeoverActive = ref(false)
  // 消息反馈 / 收藏状态：key = message_ref（mid:{id} 或 hash:{sha256[:16]}）
  // 进会话时由 listFeedback / listSessionFavorites 拉取填充；UI 按查表决定按钮 active 态
  const feedback = ref<Record<string, "up" | "down">>({})
  const favorites = ref<Set<string>>(new Set())
  // 点踩原因弹窗：点 "踩"（新值，非取消）时置为目标消息，由 DownFeedbackDialog 收 reason 后调 submitDownFeedback
  const downFeedbackTarget = ref<Message | null>(null)
  let currentRunId: string | null = null
  let _abortController: AbortController | null = null
  // 单轮耗时与 token 用量统计（run.started 起算，完成时落 _turnDuration/_turnUsage）
  let turnStartedAt = 0
  let turnUsage: any = null
  const currentModel = ref("openai/gpt-5.4-mini")
  const models = ref<string[]>([
    "openai/gpt-5.4-mini", "openai/gpt-4o", "openai/o3", "openai/o4-mini",
    "anthropic/claude-sonnet-4.6", "anthropic/claude-sonnet-4-5", "anthropic/claude-haiku-3-5",
    "google/gemini-3.1-pro-preview", "google/gemini-3-flash-preview",
    "deepseek/deepseek-v4-flash", "deepseek/deepseek-v4-pro", "deepseek/deepseek-chat-v3-0324",
    "meta-llama/llama-4-scout",
  ])
  const workspaceFiles = ref<any[]>([])
  const workspaceNames = ref<string[]>(["."])
  const currentWs = ref(".")
  // 当前选中文件的内容（只读预览）
  const fileContent = ref<any>(null)
  const fileLoading = ref(false)
  const profiles = ref<{ name: string }[]>([{ name: "default" }])
  const currentProfile = ref("default")

  const currentSession = computed(() =>
    sessions.value.find((s) => s.session_id === currentSessionId.value) || null
  )

  const filteredSessions = computed(() => {
    if (!searchQuery.value) return sessions.value
    const q = searchQuery.value.toLowerCase()
    return sessions.value.filter((s) => s.title.toLowerCase().includes(q))
  })

  async function newSession() {
    // Dify 引擎：Dify 不支持预创建会话（POST /api/sessions 会 404），
    // 本地生成 sessionId 占位，第一次对话由 Dify 自动创建 conversation，
    // SSE 首块回填 engine_conversation_id 后续多轮再用。
    if (engineType === 'DIFY' || engineType === 'dify') {
      const localId = `local-${Date.now()}-${Math.random().toString(36).slice(2, 8)}`
      const session: Session = {
        session_id: localId,
        title: "未开始",
        messages: [],
        _loaded: true,
        created_at: Date.now() / 1000,
        last_message_at: null,
        model: currentModel.value,
      }
      sessions.value.unshift(session)
      currentSessionId.value = localId
      engineAvailable.value = true
      return
    }
    try {
      const res = await gwFetch(`${GW_BASE}/api/sessions`, {
        method: "POST",
        headers: gwHeaders(agentId),
        body: JSON.stringify({ model: bareModel(currentModel.value) }),
      })
      if (!res.ok) {
        if (res.status === 503) engineAvailable.value = false
        console.error("Failed to create session:", await res.text())
        return
      }
      engineAvailable.value = true
      const data = await res.json()
      // data.session 可能包裹在 session 字段下，也可能直接是 session 对象
      const sessionData = data.session || data
      const session: Session = {
        session_id: sessionData.session_id || sessionData.id,
        title: sessionData.title || "未开始",
        messages: [],
        _loaded: true,
        created_at: sessionData.created_at || Date.now() / 1000,
        last_message_at: null,
        model: sessionData.model || currentModel.value,
      }
      sessions.value.unshift(session)
      currentSessionId.value = session.session_id
    } catch (e) {
      console.error("Error creating session:", e)
    }
  }

  async function updateSessionTitle(sessionId: string, title: string) {
    try {
      await gwFetch(`${GW_BASE}/api/sessions/${encodeURIComponent(sessionId)}`, {
        method: "PATCH",
        headers: gwHeaders(agentId),
        body: JSON.stringify({ title }),
      })
      // 同步更新本地状态
      const session = sessions.value.find((s) => s.session_id === sessionId)
      if (session) {
        session.title = title
        sessions.value = [...sessions.value]
      }
    } catch (e) {
      console.error("Error updating session title:", e)
    }
  }

  function deriveSessionTitle(messages: any[]): string | null {
    for (const m of messages) {
      if (m.role === "user") {
        const text = stripAttachmentHint(typeof m.content === "string" ? m.content : "")
        if (text) return text.slice(0, 64)
      }
    }
    return null
  }

  async function autoGenerateTitle(sessionId: string, messages: any[]) {
    if (!sessionId || messages.length < 1) return
    const session = sessions.value.find((s) => s.session_id === sessionId)
    if (!session || session.title !== "未开始") return

    // 仅用启发式截断（不从首条用户消息截取 64 字符），
    // 不走 LLM 生成以免引擎创建多余会话记录
    const title = deriveSessionTitle(messages)
    if (title && title !== "未开始") {
      session.title = title
      sessions.value = [...sessions.value]
      updateSessionTitle(sessionId, title)
    }
  }

  async function loadSessionMessages(sessionId: string): Promise<any[]> {
    try {
      const res = await gwFetch(
        `${GW_BASE}/api/sessions/${encodeURIComponent(sessionId)}/messages`,
        { headers: gwHeaders(agentId) }
      )
      if (res.ok) {
        const data = await res.json()
        const messages = data.messages || data.data || []
        // 过滤掉工具消息和空白的工具调用占位消息
        const filtered = messages.filter((m: any) =>
          m.role !== "tool" && !(m.role === "assistant" && !m.content && m.tool_calls)
        )
        // 服务端恢复反馈 / 收藏按钮态（替代 localStorage mock）
        // 失败不阻塞会话加载——按钮态只是视觉，提交时仍会按当前态发请求
        try {
          const [fb, favs] = await Promise.all([
            listFeedback(sessionId),
            listSessionFavorites(sessionId),
          ])
          const fbMap: Record<string, "up" | "down"> = {}
          for (const f of fb) {
            const r = f.message_ref
            const v = f.value
            if (r && (v === "up" || v === "down")) fbMap[r] = v
          }
          const favSet = new Set<string>(favs.map((f: any) => f.message_ref).filter(Boolean))
          feedback.value = fbMap
          favorites.value = favSet
          // 把状态刷到消息上供 UI 直接读取（msg._feedback / msg._favorite）
          for (const m of filtered) {
            if (m.role !== "assistant" || !m.content) continue
            const ref = await messageRefOf(m)
            m._feedback = fbMap[ref]
            m._favorite = favSet.has(ref)
          }
        } catch (e) {
          console.warn("[useChat] restore feedback/favorites failed:", e)
        }
        return filtered
      }
    } catch (e) {
      console.error("Error loading session messages:", e)
    }
    return []
  }

  async function selectSession(sessionId: string) {
    currentSessionId.value = sessionId
    const session = sessions.value.find((s) => s.session_id === sessionId)
    if (session && !session._loaded) {
      const msgs = await loadSessionMessages(sessionId)
      session.messages = msgs
      session._loaded = true
      sessions.value = [...sessions.value]
    }
  }

  async function loadSessions() {
    // Dify 引擎：Dify GET /v1/conversations 字段（id/name）与 hermes session 不兼容，
    // 且前端调的是 api/sessions 路径 DifyAdapter 未映射会 404。
    // 暂时跳过历史会话加载，用户每次进页面起新会话；后续单独适配 Dify 列表格式。
    if (engineType === 'DIFY' || engineType === 'dify') {
      engineAvailable.value = true
      return
    }
    try {
      const res = await gwFetch(
        `${GW_BASE}/api/sessions?limit=50`,
        { headers: gwHeaders(agentId) }
      )
      if (res.ok) {
        engineAvailable.value = true
        const data: any = await res.json()
        const list: any[] = data.sessions || data.data || data
        sessions.value = list.map((s: any) => ({
            session_id: s.session_id || s.id,
            title: s.title || s.preview || "未命名",
            messages: [],
            _loaded: false,
            created_at: s.created_at || s.started_at || 0,
            last_message_at: s.updated_at || s.last_message_at || s.started_at || null,
            model: s.model || currentModel.value,
          }))
        // 按最后活跃时间降序排列，最近的在最前面
        sessions.value.sort((a, b) => {
          const aTime = a.last_message_at || a.created_at || 0
          const bTime = b.last_message_at || b.created_at || 0
          return bTime - aTime
        })
      } else if (res.status === 503) {
        engineAvailable.value = false
      }
    } catch (e) {
      console.error("Error loading sessions:", e)
    }
  }

  async function loadModels() {
    try {
      // 优先从 Controller 获取 Agent 配置的模型列表
      const data = await getAgentModels(agentId)
      if (data?.data?.length > 0) {
        const modelIds = data.data.map((m: any) => m.id)
        models.value = modelIds
        // 同步默认选中的模型：当前选的不在列表里则切到第一个
        if (!modelIds.includes(currentModel.value)) {
          currentModel.value = modelIds[0]
        }
        return
      }
    } catch (e) {
      console.debug("Controller models not available, falling back to engine /v1/models", e)
    }

    // Fallback: 从引擎获取 /v1/models
    try {
      const res = await gwFetch(`${GW_BASE}/v1/models`, {
        headers: gwHeaders(agentId),
      })
      if (res.ok) {
        const data = await res.json()
        if (data?.data?.length > 0) {
          models.value = data.data.map((m: any) => m.id)
        }
      }
    } catch (e) {
      console.error("Error loading models from engine:", e)
    }
  }

  async function sendMessage(text: string, files?: File[], opts: { isRetry?: boolean; attempt?: number } = {}) {
    // HERMES 引擎走原生 /v1/runs 流（thinking/tool/approval 事件只在 /v1/runs/{id}/events 上发），
    // gateway catch-all 透传 /v1/runs/* 到 Hermes；其它引擎走 /v1/chat/completions。
    if (engineType === 'HERMES') {
      return sendHermesRun(text, files, opts)
    }
    const attempt = opts.attempt || 0
    if (!currentSessionId.value) {
      await newSession()
    }

    const sessionId = currentSessionId.value
    if (!sessionId) return

    const session = sessions.value.find((s) => s.session_id === sessionId)
    if (!session) return

    // 上传附件（非重试时）
    let attachments: Attachment[] | undefined
    let displayText = text
    if (files && files.length > 0 && !opts.isRetry) {
      attachments = await uploadFiles(files, sessionId, agentId, engineType)
      if (!text) {
        displayText = `Uploaded: ${attachments.map(a => a.name).join(", ")}`
      }
    }

    // 重试（isRetry）时不重复推 user 消息，复用已存在的
    if (!opts.isRetry) {
      session.messages = [...session.messages, { role: "user", content: displayText, attachments, _ts: Date.now() / 1000 }]
      sessions.value = [...sessions.value]
      // Generate title immediately from first user message so the session list
      // doesn't show "未开始" until the AI reply finishes.
      autoGenerateTitle(sessionId, session.messages)
    }
    isStreaming.value = true
    streamingContent.value = ""
    thinkingText.value = ""
    thinkingStatus.value = 'pending'
    toolCalls.value = []
    activityEvents.value = []
    turnStartedAt = Date.now()
    turnUsage = null
    let schedulingRetry = false

    function addActivity(kind: string, label: string, detail: string = '', status: string = 'waiting') {
      activityEvents.value.push({ kind, label, detail, status, ts: Date.now() / 1000 })
    }

    addActivity('run', attempt > 0 ? `重连 ${attempt}/${MAX_AUTO_RETRIES}` : '启动智能体', '正在建立连接并发送消息…')
    let fullContent = ""
    let rawContent = ""

    // Create abort controller for stop support
    _abortController = new AbortController()

    try {
      // Dify 多轮：第二轮起把 SSE 首块里的 engine_conversation_id 透传给网关，
      // 由 DifyAdapter.transform_headers 弹出 X-Session-ID → X-Dify-Conversation-Id，
      // 再由 transform_request_body 写到 body.conversation_id。
      // 第一轮 engine_conversation_id 为空 → sidForDify="" → 不设 X-Session-ID 头
      // → Dify 自动创建新 conversation，SSE 首块回填 engine_conversation_id。
      // Hermes 走 /v1/runs 不经此分支。
      const sidForDify = session.engine_conversation_id || ""
      const res = await gwFetch(`${GW_BASE}/v1/chat/completions`, {
        method: "POST",
        headers: gwHeaders(agentId, { sessionId: sidForDify, engineType }),
        signal: _abortController.signal,
        body: JSON.stringify({
          model: bareModel(currentModel.value),
          messages: session.messages.map((m) => toEngineMessage(m)),
          stream: true,
          stream_options: { include_usage: true },
          user: useAuthStore().user?.id,
        }),
      })

      if (!res.ok) {
        if (res.status === 503) engineAvailable.value = false
        const errBody = await res.text().catch(() => '')
        addActivity('warning', 'Error', `${res.status} - ${errBody}`, 'error')
        const err: any = new Error(`HTTP ${res.status}`)
        err.status = res.status
        throw err
      }

      addActivity('run', '已连接', '', 'done')
      addActivity('model', `模型: ${currentModel.value}`, '', 'done')
      addActivity('waiting', '等待回复', '已连接，等待模型输出…')

      // Parse SSE stream via body reader
      let readerErr = ''
      let chunkCount = 0
      try {
        const reader = res.body?.getReader()
        if (reader) {
          const decoder = new TextDecoder()
          let buf = ''
          while (true) {
            const result = await reader.read()
            if (result.done) break
            chunkCount++
            buf += decoder.decode(result.value, { stream: true })
            const lines = buf.split('\n')
            buf = lines.pop() || ''
            for (const raw of lines) {
              const t = raw.trim()
              if (!t.startsWith('data: ')) continue
              const d = t.slice(6)
              if (d === '[DONE]' || !d.startsWith('{')) continue
              try {
                const p = JSON.parse(d)

                // gateway.silence 看门狗帧：更新静默计时 + "等待回复"事件 detail，跳过后续分发
                if (p.event === "gateway.silence") {
                  silenceElapsedSeconds.value = (p.elapsed as number) || null
                  updateWaitingSilenceDetail(silenceElapsedSeconds.value)
                  continue
                }
                // 其他事件到达即代表有活动，清零静默计时
                if (silenceElapsedSeconds.value !== null) silenceElapsedSeconds.value = null

                // Detect tool progress events
                if (p.tool && p.status) {
                  const tName: string = p.label || p.tool || 'unknown'
                  if (p.status === 'running') {
                    const idx = toolCalls.value.length
                    const fullLabel: string = p.label || ''
                    const shortPreview = fullLabel.length > 120 ? fullLabel.slice(0, 117) + '…' : fullLabel
                    toolCalls.value.push({
                      index: idx,
                      name: p.tool || 'tool',
                      status: 'running' as const,
                      statusLabel: 'running',
                      result: '',
                      args: {},
                      preview: shortPreview,
                      tid: p.toolCallId || `tool-${idx}`,
                    })
                    // 工具生命周期只走 toolCalls（ToolCard 渲染），不再写 activity 事件
                    // （对齐 Android：feed 里没有 "Tool finished" 这类行；等待事件保留，
                    // 由首个正文 delta 标记"已回复"）
                  } else if (p.status === 'completed' || p.status === 'success') {
                    const existing = toolCalls.value.find(t => t.tid === p.toolCallId || t.name === p.tool)
                    if (existing) {
                      existing.status = 'success'
                      existing.statusLabel = 'completed'
                    }
                  }
                  continue
                }

                // ── run 生命周期 + 审批工作流事件（嫁接自 Repo1）──
                if (p.type === "run.start" && p.run_id) {
                  currentRunId = p.run_id as string
                  registerPendingRun(currentRunId, sessionId)
                  turnStartedAt = Date.now()
                  continue
                }
                if (p.type === "approval.request") {
                  approvalPending.value = {
                    runId: p.run_id || currentRunId || "",
                    command: p.command || "",
                    description: p.description || "",
                    choices: p.choices || ["once", "session", "always", "deny"],
                    status: "pending"
                  }
                  addActivity("warning", "需审批", p.description || p.command || "", "waiting")
                  continue
                }
                if (p.type === "approval.responded") {
                  if (approvalPending.value) {
                    approvalPending.value.status = "responded"
                    approvalPending.value.choice = p.choice as ApprovalChoice
                    approvalPending.value.submitting = false
                  }
                  addActivity("run", "审批已响应", String(p.choice || ""), "done")
                  continue
                }

                const delta = p.choices?.[0]?.delta
                const finish = p.choices?.[0]?.finish_reason
                // OpenAI 流式末帧（stream_options.include_usage=true）带 usage
                if (p.usage) turnUsage = p.usage

                // Provider-native reasoning (e.g. DeepSeek reasoning_content, Qwen reasoning)
                const reasoningChunk = delta?.reasoning_content || delta?.reasoning
                if (reasoningChunk) {
                  thinkingText.value += reasoningChunk
                  thinkingStatus.value = 'thinking'
                }

                // Regular content with <think> tag parsing
                const ct = delta?.content || ''
                if (ct) {
                  rawContent += ct
                  // Re-parse <think> blocks from accumulated raw content
                  let displayContent = rawContent
                  let thinkText = ''
                  // Completed <think>...</think> blocks
                  displayContent = displayContent.replace(/<think>([\s\S]*?)<\/think>/g, (_: string, t: string) => {
                    thinkText += t
                    return ''
                  })
                  // Unclosed <think> (still streaming)
                  const openMatch = displayContent.match(/<think>([\s\S]*)$/)
                  if (openMatch) {
                    thinkText += openMatch[1]
                    displayContent = displayContent.replace(/<think>[\s\S]*$/, '')
                  }
                  if (thinkText) {
                    thinkingText.value = thinkText
                    thinkingStatus.value = 'thinking'
                  }
                  fullContent = displayContent
                  streamingContent.value = displayContent
                  if (!thinkText && !reasoningChunk) thinkingStatus.value = 'done'
                }

                // Dify 集成模式：SSE chunk 里塞了 conversation_id，首轮必含。
                // 捕获后存到 session，后续请求经 X-Session-Id 头透传给 gateway，
                // 由 DifyAdapter.transform_headers 转为 X-Dify-Conversation-Id。
                if (p.conversation_id && !session.engine_conversation_id) {
                  session.engine_conversation_id = p.conversation_id
                  sessions.value = [...sessions.value]
                }

                // Stream complete — mark tool calls done
                if (finish === 'stop' && toolCalls.value.length > 0) {
                  for (const t of toolCalls.value) {
                    if (t.status === 'running') { t.status = 'success'; t.statusLabel = 'completed' }
                  }
                }
              } catch (ex) { readerErr = 'parse:' + String(ex) }
            }
          }
          if (!fullContent && chunkCount === 0) readerErr = 'empty_chunks'
        } else {
          // Fallback
          const txt = await res.text()
          for (const raw of txt.split('\n')) {
            const t = raw.trim()
            if (!t.startsWith('data: ')) continue
            const d = t.slice(6)
            if (d === '[DONE]' || !d.startsWith('{')) continue
            try {
              const p = JSON.parse(d)
              const ct = p.choices?.[0]?.delta?.content || ''
              if (ct) fullContent += ct
            } catch {}
          }
        }
      } catch (e2: any) {
        readerErr = String(e2.message || e2)
      }
      if (readerErr) console.warn('[useChat] reader error:', readerErr, 'full:', fullContent.length)
      // 无内容收到 + reader 出错 → 抛给外层 catch 走重试/落错逻辑
      if (!fullContent && readerErr && !readerErr.startsWith('empty_chunks')) {
        const err: any = new Error(readerErr)
        throw err
      }
      // Push final assistant message
      if (fullContent) {
        session.messages = [...session.messages, { role: "assistant", content: fullContent, _turnUsage: turnUsage || null, _turnDuration: turnStartedAt ? (Date.now() - turnStartedAt) / 1000 : undefined, _model: currentModel.value, _ts: Date.now() / 1000, _toolCalls: [...toolCalls.value], _thinkingText: thinkingText.value, _activityEvents: [...activityEvents.value], _runId: currentRunId || undefined }]
        sessions.value = [...sessions.value]
        autoGenerateTitle(sessionId, session.messages)
      }
    } catch (e: any) {
      if (e.name === 'AbortError') {
        // 用户主动停止，不重试不落错
      } else {
        const cls = classifySendError(e, e.status)
        const hasContent = !!fullContent
        // 仅连接阶段（无内容）+ 可重试 + 未超次数 → 自动重连
        if (cls.retryable && !hasContent && attempt < MAX_AUTO_RETRIES) {
          addActivity('waiting', `重连中 ${attempt + 1}/${MAX_AUTO_RETRIES}`, `${cls.kind} 错误，${retryDelay(attempt)}ms 后重试…`)
          schedulingRetry = true
          setTimeout(() => sendMessage(text, undefined, { isRetry: true, attempt: attempt + 1 }), retryDelay(attempt))
        } else {
          // 不可重试 / 已有内容 / 重试耗尽 → 落 error 消息（_retryable 决定 UI 是否显示重试按钮）
          session.messages = [...session.messages, { role: "assistant", isError: true, content: `Error: ${e.message || e.status || '未知错误'}`, provider_details: String(e.message || e), _retryable: !hasContent && cls.retryable, _retryKind: cls.kind, _activityEvents: [...activityEvents.value] }]
          sessions.value = [...sessions.value]
          autoGenerateTitle(sessionId, session.messages)
        }
      }
    } finally {
      if (!schedulingRetry) {
        // Strip thinking echo: some models repeat the answer start in reasoning
        if (thinkingText.value && fullContent) {
          const thinkTail = thinkingText.value.slice(-80).trim()
          if (thinkTail && fullContent.trim().startsWith(thinkTail)) {
            thinkingText.value = thinkingText.value.slice(0, -80)
          }
        }
        isStreaming.value = false
        streamingContent.value = ""
        thinkingStatus.value = fullContent ? 'done' : 'pending'
        settleWaitingEvents(!!fullContent)
      }
      // 流结束（正常/异常/abort）：清除 pending run 与审批态（重试会重建）
      clearPendingRun(currentRunId)
      approvalPending.value = null
      silenceElapsedSeconds.value = null
      currentRunId = null
      _abortController = null
    }
  }

  function stopStreaming() {
    if (_abortController) {
      _abortController.abort()
      _abortController = null
    }
    isStreaming.value = false
    streamingContent.value = ""
    thinkingStatus.value = 'done'
  }

  /** 手动重试一条失败的消息：移除 error 消息，用其前一条 user 消息内容重发（不重复推 user 消息） */
  async function retryMessage(errorMsg: any) {
    const sessionId = currentSessionId.value
    if (!sessionId) return
    const session = sessions.value.find((s) => s.session_id === sessionId)
    if (!session) return
    const msgs = session.messages
    const idx = msgs.indexOf(errorMsg)
    if (idx < 0) return
    // 移除该 error 消息（及之后任何残留）
    session.messages = msgs.slice(0, idx)
    sessions.value = [...sessions.value]
    // 找前一条 user 消息内容
    let retryText = ""
    for (let i = session.messages.length - 1; i >= 0; i--) {
      if (session.messages[i].role === 'user') { retryText = session.messages[i].content; break }
    }
    if (!retryText) return
    await sendMessage(retryText, undefined, { isRetry: true })
  }

  /** 编辑最后一条用户消息：截断该消息之后的历史，用新文本重发 */
  async function editMessage(msg: any, newText: string) {
    const sessionId = currentSessionId.value
    if (!sessionId) return
    const session = sessions.value.find((s) => s.session_id === sessionId)
    if (!session) return
    const idx = session.messages.indexOf(msg)
    if (idx < 0) return
    session.messages = session.messages.slice(0, idx)
    sessions.value = [...sessions.value]
    await sendMessage(newText, undefined, { isRetry: true })
  }

  /** 重新生成最后一条 assistant 回复：截断该回复及之后的历史，用前一条 user 消息重发 */
  async function regenerateResponse(msg: any) {
    const sessionId = currentSessionId.value
    if (!sessionId) return
    const session = sessions.value.find((s) => s.session_id === sessionId)
    if (!session) return
    const idx = session.messages.indexOf(msg)
    if (idx < 0) return
    session.messages = session.messages.slice(0, idx)
    sessions.value = [...sessions.value]
    let userText = ""
    for (let i = session.messages.length - 1; i >= 0; i--) {
      if (session.messages[i].role === 'user') { userText = session.messages[i].content; break }
    }
    if (!userText) return
    await sendMessage(userText, undefined, { isRetry: true })
  }

  /** 清空当前会话：删除引擎端会话 + 创建新会话 + 从列表移除旧会话 */
  async function clearConversation() {
    const sid = currentSessionId.value
    if (!sid) return
    const session = sessions.value.find((s) => s.session_id === sid)
    if (!session) return
    try {
      await gwFetch(`${GW_BASE}/api/sessions/${encodeURIComponent(sid)}`, {
        method: "DELETE",
        headers: gwHeaders(agentId, { sessionId: sid, engineType }),
      })
    } catch (e) {
      console.warn("[useChat] clearConversation: failed to delete session on engine:", e)
    }
    await newSession()
    sessions.value = sessions.value.filter((s) => s.session_id !== sid)
  }

  /**
   * 设置消息反馈 👍👎（manager API 持久化 + 乐观更新 + 失败回滚）。
   * - 点赞 / 取消赞 / 取消踩：直接走这里
   * - 点踩（新值）：UI 弹 DownFeedbackDialog 收 reason 后调 submitDownFeedback
   */
  async function setFeedback(msg: Message, rating: "up" | "down") {
    const sessionId = currentSessionId.value
    if (!sessionId || !msg || msg.role !== "assistant" || !msg.content) return
    // 点踩（新值）：UI 应拦截弹窗，不应走到这里；防御性兜底也直接弹
    if (rating === "down" && msg._feedback !== "down") {
      downFeedbackTarget.value = msg
      return
    }
    const ref = await messageRefOf(msg)
    const current = msg._feedback
    const next = current === rating ? undefined : rating
    applyFeedbackToState(ref, next, msg)
    try {
      await upsertFeedback({
        agent_id: agentId, session_id: sessionId, message_ref: ref,
        run_id: msg._runId || null, value: next || null,
        content_snapshot: msg.content,
      })
    } catch (e) {
      console.warn("[useChat] set feedback failed:", e)
      applyFeedbackToState(ref, current, msg)
    }
  }

  /** 点踩提交（DownFeedbackDialog 收 reason 后调用）。reason 必填。 */
  async function submitDownFeedback(msg: Message, reason: string, comment: string | null) {
    const sessionId = currentSessionId.value
    if (!sessionId || !msg || msg.role !== "assistant" || !msg.content) return
    const ref = await messageRefOf(msg)
    const current = msg._feedback
    applyFeedbackToState(ref, "down", msg)
    downFeedbackTarget.value = null
    try {
      await upsertFeedback({
        agent_id: agentId, session_id: sessionId, message_ref: ref,
        run_id: msg._runId || null, value: "down", reason: reason as any,
        comment: comment || null, content_snapshot: msg.content,
      })
    } catch (e) {
      console.warn("[useChat] submit down feedback failed:", e)
      applyFeedbackToState(ref, current, msg)
    }
  }

  function closeDownFeedbackDialog() {
    downFeedbackTarget.value = null
  }

  /** 收藏 / 取消收藏（manager API 持久化 + 乐观更新 + 失败回滚） */
  async function setFavorite(msg: Message, favored: boolean) {
    const sessionId = currentSessionId.value
    if (!sessionId || !msg || msg.role !== "assistant" || !msg.content) return
    const ref = await messageRefOf(msg)
    const wasFavored = !!msg._favorite
    if (favored === wasFavored) return // 同态切换无意义
    applyFavoriteToState(ref, favored, msg)
    try {
      if (favored) {
        await upsertFavorite({
          agent_id: agentId, session_id: sessionId, message_ref: ref,
          run_id: msg._runId || null, content_snapshot: msg.content,
        })
      } else {
        await deleteFavorite({ session_id: sessionId, message_ref: ref })
      }
    } catch (e) {
      console.warn("[useChat] toggle favorite failed:", e)
      applyFavoriteToState(ref, wasFavored, msg)
    }
  }

  // 同时更新 favorites set（canonical）与 msg._favorite（兼容既有 UI 模板的 active 判定）
  function applyFeedbackToState(ref: string, value: "up" | "down" | undefined, msg: Message) {
    const next = { ...feedback.value }
    if (value) next[ref] = value
    else delete next[ref]
    feedback.value = next
    msg._feedback = value
    sessions.value = [...sessions.value]
  }

  function applyFavoriteToState(ref: string, favored: boolean, msg: Message) {
    const next = new Set(favorites.value)
    if (favored) next.add(ref)
    else next.delete(ref)
    favorites.value = next
    msg._favorite = favored
    sessions.value = [...sessions.value]
  }

  // ── HERMES /v1/runs flow：thinking + tool 生命周期 + 审批（回退自 V1，Hermes 原生流）──
  function handleHermesEvent(p: any) {
    // gateway.silence 看门狗帧：更新静默计时 + "等待回复"事件 detail，不进入活动事件分发
    if (p.event === "gateway.silence") {
      silenceElapsedSeconds.value = (p.elapsed as number) || null
      updateWaitingSilenceDetail(silenceElapsedSeconds.value)
      return
    }
    // 其他事件到达即代表有活动，清零静默计时
    if (silenceElapsedSeconds.value !== null) silenceElapsedSeconds.value = null
    switch (p.event) {
      case 'run.started':
        turnStartedAt = Date.now()
        break
      case 'message.delta':
        streamingContent.value += p.delta || ''
        thinkingStatus.value = 'done'
        break
      case 'reasoning.available': {
        // Hermes engine (conversation_loop.py:3378) emits reasoning.available with
        // assistant_message.content[:500] for ALL top-level agents, even when the model
        // has no separate reasoning. This causes the thinking box to show the same text
        // as the regular output. Skip when the reasoning text matches the streaming content.
        const rText = (p.text || '').trim()
        const sText = streamingContent.value.trim()
        if (rText && sText && isReasoningEchoOfReply(rText, sText)) {
          break
        }
        thinkingText.value += p.text || ''
        thinkingStatus.value = 'thinking'
        break
      }
      case 'tool.started': {
        const idx = toolCalls.value.length
        const preview: string = p.preview || ''
        const shortPreview = preview.length > 120 ? preview.slice(0, 117) + '…' : preview
        toolCalls.value.push({
          index: idx, name: p.tool || 'tool', status: 'running', statusLabel: 'running',
          result: '', args: {}, preview: shortPreview, tid: `hermes-tool-${idx}`,
        })
        // 工具事件不写 activity feed（对齐 Android：只出 ToolCard）；等待事件保留
        break
      }
      case 'tool.completed': {
        const existing = [...toolCalls.value].reverse().find((t: any) => t.name === p.tool && t.status === 'running')
        if (existing) {
          existing.status = p.error ? 'error' : 'success'
          existing.statusLabel = p.error ? 'error' : 'completed'
        }
        break
      }
      case 'approval.request':
        approvalPending.value = {
          runId: currentRunId || '',
          command: p.command || '',
          description: p.description || '',
          choices: (p.choices || ['once', 'session', 'always', 'deny']) as ApprovalChoice[],
          status: 'pending',
        }
        activityEvents.value = activityEvents.value.filter((e: any) => e.kind !== 'waiting')
        activityEvents.value.push({ kind: 'warning', label: '危险操作待确认', detail: p.description || p.command || '', status: 'waiting', ts: Date.now() / 1000 })
        break
      case 'approval.responded':
        if (approvalPending.value) {
          approvalPending.value.status = 'responded'
          approvalPending.value.choice = p.choice as ApprovalChoice
          approvalPending.value.submitting = false
        }
        activityEvents.value = activityEvents.value.map((ev: any) =>
          ev.kind === 'warning' && ev.status === 'waiting' ? { ...ev, status: 'done', label: `已响应：${p.choice}`, detail: '' } : ev
        )
        break
      case 'run.completed':
        if (p.output && !streamingContent.value) streamingContent.value = p.output
        if (p.usage) turnUsage = p.usage
        thinkingStatus.value = 'done'
        for (const t of toolCalls.value) { if ((t as any).status === 'running') { t.status = 'success'; t.statusLabel = 'completed' } }
        settleWaitingEvents(true)
        break
      case 'run.failed':
        // 引擎侧失败（如 LLM provider 未配置、上游 401/5xx）必须落一条 error assistant
        // 消息到对话流，否则前端只把 warning 塞 activity feed，主对话区一片空白，
        // 用户看到的是"无响应"而非明确的错误原因。
        if (p.error) {
          activityEvents.value.push({ kind: 'warning', label: '运行失败', detail: String(p.error), status: 'error', ts: Date.now() / 1000 })
          const sid = currentSessionId.value
          const session = sid ? sessions.value.find((s) => s.session_id === sid) : null
          if (session) {
            session.messages = [...session.messages, { role: "assistant", isError: true, content: `引擎运行失败：${p.error}`, provider_details: String(p.error) }]
            sessions.value = [...sessions.value]
          }
          thinkingStatus.value = 'done'
          streamingContent.value = ""
        }
        break
      case 'run.cancelled':
        break
      default:
        break
    }
  }

  async function consumeHermesRunStream(runId: string) {
    const resp = await gwFetch(`${GW_BASE}/v1/runs/${runId}/events`, {
      headers: gwHeaders(agentId, { sessionId: currentSessionId.value, engineType }),
      signal: _abortController?.signal,
    })
    if (!resp.ok) throw new Error(`SSE stream failed: ${resp.status}`)
    const reader = resp.body?.getReader()
    if (!reader) return
    const decoder = new TextDecoder()
    let buf = ''
    while (true) {
      const { done, value } = await reader.read()
      if (done) break
      buf += decoder.decode(value, { stream: true })
      const lines = buf.split('\n')
      buf = lines.pop() || ''
      for (const raw of lines) {
        const t = raw.trim()
        if (!t.startsWith('data: ')) continue
        const d = t.slice(6)
        if (d === '[DONE]' || !d.startsWith('{')) continue
        try { handleHermesEvent(JSON.parse(d)) } catch (e) { console.warn('[useChat] hermes event parse error:', e, d) }
      }
    }
  }

  async function sendHermesRun(text: string, files?: File[], opts: { isRetry?: boolean; attempt?: number } = {}) {
    const attempt = opts.attempt || 0
    if (!currentSessionId.value) await newSession()
    const sessionId = currentSessionId.value
    if (!sessionId) return
    const session = sessions.value.find((s) => s.session_id === sessionId)
    if (!session) return

    // 上传附件（非重试时）
    let attachments: Attachment[] | undefined
    let displayText = text
    if (files && files.length > 0 && !opts.isRetry) {
      attachments = await uploadFiles(files, sessionId, agentId, engineType)
      if (!text) {
        displayText = `Uploaded: ${attachments.map(a => a.name).join(", ")}`
      }
    }

    if (!opts.isRetry) {
      session.messages = [...session.messages, { role: "user", content: displayText, attachments, _ts: Date.now() / 1000 }]
      sessions.value = [...sessions.value]
      // Generate title immediately from first user message so the session list
      // doesn't show "未开始" until the AI reply finishes.
      autoGenerateTitle(sessionId, session.messages)
    }
    isStreaming.value = true
    streamingContent.value = ""
    thinkingText.value = ""
    thinkingStatus.value = 'pending'
    toolCalls.value = []
    activityEvents.value = []
    approvalPending.value = null
    _abortController = new AbortController()
    let schedulingRetry = false

    function addActivity(kind: string, label: string, detail: string = '', status: string = 'waiting') {
      activityEvents.value.push({ kind, label, detail, status, ts: Date.now() / 1000 })
    }
    addActivity('run', attempt > 0 ? `重连 ${attempt}/${MAX_AUTO_RETRIES}` : '启动智能体', '正在建立连接并发送消息…')
    let fullContent = ""

    try {
      // 1. POST /v1/runs 启动 run
      const startResp = await gwFetch(`${GW_BASE}/v1/runs`, {
        method: "POST",
        headers: gwHeaders(agentId, { sessionId, engineType }),
        signal: _abortController.signal,
        body: JSON.stringify({
          input: text,
          ...(attachments && attachments.length ? { attachments } : {}),
          session_id: sessionId,
          conversation_history: session.messages
            .filter((m: any) => (m.role === 'user' || m.role === 'assistant') && !m.isError)
            .slice(0, -1)
            .map((m: any) => toEngineMessage(m)),
          model: bareModel(currentModel.value),
          user: useAuthStore().user?.id,
        }),
      })
      if (!startResp.ok) {
        const errText = await startResp.text().catch(() => '')
        addActivity('warning', 'Error', `${startResp.status} - ${errText}`, 'error')
        const err: any = new Error(`HTTP ${startResp.status}`)
        err.status = startResp.status
        throw err
      }
      const startData = await startResp.json()
      const runId = startData.run_id
      if (!runId) {
        addActivity('warning', 'Error', 'No run_id in response', 'error')
        session.messages = [...session.messages, { role: "assistant", isError: true, content: 'Error: No run_id returned' }]
        return
      }
      currentRunId = runId
      registerPendingRun(runId, sessionId)
      turnStartedAt = Date.now()
      turnUsage = null
      addActivity('run', '已连接', '', 'done')
      addActivity('model', `模型: ${currentModel.value}`, '', 'done')
      addActivity('waiting', '等待回复', '已连接，等待模型输出…')

      // 2. GET /v1/runs/{run_id}/events — SSE 流（持续到 run 结束）
      await consumeHermesRunStream(runId)

      // 3. 落定最终内容
      if (streamingContent.value) {
        fullContent = streamingContent.value
        session.messages = [...session.messages, { role: "assistant", content: fullContent, _turnUsage: turnUsage || null, _turnDuration: turnStartedAt ? (Date.now() - turnStartedAt) / 1000 : undefined, _model: currentModel.value, _ts: Date.now() / 1000, _toolCalls: [...toolCalls.value], _thinkingText: thinkingText.value, _activityEvents: [...activityEvents.value], _runId: currentRunId || undefined }]
        sessions.value = [...sessions.value]
        autoGenerateTitle(sessionId, session.messages)
      }
    } catch (e: any) {
      if (e.name === 'AbortError') {
        // 用户主动停止
      } else {
        const cls = classifySendError(e, e.status)
        const hasContent = !!fullContent
        if (cls.retryable && !hasContent && attempt < MAX_AUTO_RETRIES) {
          addActivity('waiting', `重连中 ${attempt + 1}/${MAX_AUTO_RETRIES}`, `${cls.kind} 错误，${retryDelay(attempt)}ms 后重试…`)
          schedulingRetry = true
          setTimeout(() => sendHermesRun(text, undefined, { isRetry: true, attempt: attempt + 1 }), retryDelay(attempt))
        } else {
          session.messages = [...session.messages, { role: "assistant", isError: true, content: `Error: ${e.message || e.status || '未知错误'}`, provider_details: String(e.message || e), _retryable: !hasContent && cls.retryable, _retryKind: cls.kind }]
          sessions.value = [...sessions.value]
          autoGenerateTitle(sessionId, session.messages)
        }
      }
    } finally {
      if (currentRunId) clearPendingRun(currentRunId)
      if (!schedulingRetry) {
        isStreaming.value = false
        streamingContent.value = ""
        thinkingStatus.value = fullContent ? 'done' : (thinkingText.value ? 'done' : 'pending')
      }
      approvalPending.value = null
      silenceElapsedSeconds.value = null
      _abortController = null
      currentRunId = null
    }
  }

  async function deleteSession(sessionId: string) {
    try {
      const res = await gwFetch(`${GW_BASE}/api/sessions/${encodeURIComponent(sessionId)}`, {
        method: "DELETE",
        headers: gwHeaders(agentId),
      })
      if (res.ok || res.status === 404) {
        sessions.value = sessions.value.filter((s) => s.session_id !== sessionId)
        if (currentSessionId.value === sessionId) {
          currentSessionId.value = sessions.value.length > 0 ? sessions.value[0].session_id : null
        }
      }
    } catch (e) {
      console.error("Error deleting session:", e)
    }
  }

  async function switchWorkspace(ws: string) {
    // 归一化：根工作区（"/" 或空）统一用 "."（相对 profile home）
    currentWs.value = !ws || ws === "/" ? "." : ws
    fileContent.value = null
    await getWorkspaceFiles()
  }

  function switchProfile(p: string) {
    currentProfile.value = p
  }

  // ── pending run 持久化 + 中断恢复 ──
  function registerPendingRun(runId: string, sessionId: string) {
    const runs = readPendingRuns()
    if (!runs.some(r => r.run_id === runId)) {
      runs.push({ run_id: runId, session_id: sessionId, agent_id: agentId, started_at: Date.now() })
      writePendingRuns(runs)
    }
  }

  function clearPendingRun(runId: string | null) {
    if (!runId) return
    const runs = readPendingRuns().filter(r => r.run_id !== runId)
    writePendingRuns(runs)
  }

  /** 提交审批选择 → POST /v1/runs/{runId}/approval（Hermes 原生），等 approval.responded 事件回流 */
  async function submitApproval(choice: ApprovalChoice) {
    const runId = approvalPending.value?.runId || currentRunId
    if (!runId || !approvalPending.value) return
    approvalPending.value.submitting = true
    try {
      const resp = await gwFetch(`${GW_BASE}/v1/runs/${runId}/approval`, {
        method: "POST",
        headers: gwHeaders(agentId, { sessionId: currentSessionId.value, engineType }),
        body: JSON.stringify({ choice, all: false }),
      })
      if (!resp.ok) {
        console.warn("[useChat] approval submit failed:", resp.status, await resp.text().catch(() => ''))
        if (approvalPending.value) approvalPending.value.submitting = false
      }
      // 不本地翻转 status —— 等 approval.responded SSE 事件
    } catch (e) {
      console.error("[useChat] approval submit error:", e)
      if (approvalPending.value) approvalPending.value.submitting = false
    }
  }

  /**
   * 挂载时续接未完成的 pending HERMES run（页面刷新/关闭中断后自动恢复）。
   * GET /v1/runs/{id} 查状态，若仍 running/waiting_for_approval/queued 则重开 /v1/runs/{id}/events SSE 流。
   */
  async function resumePendingRuns() {
    if (engineType !== 'HERMES') return
    const runs = readPendingRuns()
    for (const r of runs) {
      if (r.agent_id !== agentId) continue
      try {
        const resp = await gwFetch(`${GW_BASE}/v1/runs/${r.run_id}`, {
          headers: gwHeaders(agentId, { sessionId: r.session_id, engineType }),
        })
        if (!resp.ok) { clearPendingRun(r.run_id); continue }
        const status = await resp.json()
        const st = status.status
        if (st === 'running' || st === 'waiting_for_approval' || st === 'queued') {
          currentRunId = r.run_id
          currentSessionId.value = r.session_id
          isStreaming.value = true
          streamingContent.value = ""
          thinkingText.value = ""
          thinkingStatus.value = 'pending'
          toolCalls.value = []
          activityEvents.value = []
          approvalPending.value = null
          _abortController = new AbortController()
          const session = sessions.value.find((s) => s.session_id === r.session_id)
          if (session) {
            try {
              turnStartedAt = 0
              turnUsage = null
              await consumeHermesRunStream(r.run_id)
              if (streamingContent.value) {
                session.messages = [...session.messages, { role: "assistant", content: streamingContent.value, _turnUsage: turnUsage || null, _turnDuration: turnStartedAt ? (Date.now() - turnStartedAt) / 1000 : undefined, _model: currentModel.value, _ts: Date.now() / 1000, _toolCalls: [...toolCalls.value], _thinkingText: thinkingText.value, _activityEvents: [...activityEvents.value] }]
                sessions.value = [...sessions.value]
              }
            } catch (e) {
              console.warn('[useChat] resume stream failed:', e)
            }
          }
          clearPendingRun(r.run_id)
          isStreaming.value = false
          streamingContent.value = ""
          currentRunId = null
          _abortController = null
        } else {
          clearPendingRun(r.run_id)
        }
      } catch {
        clearPendingRun(r.run_id)
      }
    }
  }

  async function getWorkspaceFiles() {
    try {
      const data = await listAgentFiles(agentId, currentWs.value || ".")
      workspaceFiles.value = data.entries || []
      if (data.error) {
        fileContent.value = null
      }
    } catch (e: any) {
      console.error("Error loading workspace files:", e)
      workspaceFiles.value = []
    }
  }

  /** 切换到子目录（rel 相对 profile 工作区根）；传 "." 回根 */
  async function navigateWorkspace(rel: string) {
    currentWs.value = rel || "."
    fileContent.value = null
    await getWorkspaceFiles()
  }

  /** 读取文件内容（只读预览） */
  async function readFileContent(rel: string) {
    fileLoading.value = true
    try {
      const data = await readAgentFileContent(agentId, rel)
      fileContent.value = { ...data, path: rel }
    } catch (e: any) {
      fileContent.value = { path: rel, error: e?.message || "读取失败", is_text: false, content: null, content_b64: null, is_image: false, is_markdown: false, truncated: false, size: 0, name: rel.split("/").pop() || rel, max_bytes: 400000 }
    } finally {
      fileLoading.value = false
    }
  }

  return {
    sessions, currentSessionId, currentSession, filteredSessions,
    searchQuery, isStreaming, streamingContent, currentModel, models,
    workspaceFiles, workspaceNames, currentWs,
    fileContent, fileLoading,
    profiles, currentProfile,
    thinkingText, thinkingStatus, toolCalls, activityEvents,
    engineAvailable,
    approvalPending,
    silenceElapsedSeconds,
    browserTakeoverActive,
    feedback, favorites, downFeedbackTarget,
    newSession, selectSession, sendMessage, stopStreaming, retryMessage, editMessage, regenerateResponse,
    setFeedback, submitDownFeedback, closeDownFeedbackDialog, setFavorite,
    loadSessions, loadModels,
    loadSessionMessages, updateSessionTitle, autoGenerateTitle, deleteSession, deriveSessionTitle, clearConversation,
    switchWorkspace, switchProfile, getWorkspaceFiles, navigateWorkspace, readFileContent,
    submitApproval, resumePendingRuns,
  }
}
