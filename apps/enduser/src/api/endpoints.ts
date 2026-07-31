/** API 端点封装 */
import { api } from "./client"

// ── Types ────────────────────────────────────────────────

export interface AuthToken {
  access_token: string
  refresh_token: string
  token_type: string
}

export interface UserInfo {
  id: string
  username: string
  email: string
}

export interface AccessibleAgent {
  id: string
  name: string
  description: string
  engine_type: "HERMES" | "OPENCLAW" | "DIFY" | "CLAUDE_CODE"
  last_accessed_at: string | null
  browser_sandbox_enabled?: boolean
}

export interface AgentDeploymentStatus {
  agent_id: string
  status: "PENDING" | "DEPLOYING" | "RUNNING" | "SUSPENDED" | "FAILED" | "ARCHIVED"
  engine_url: string | null
  last_active_at: string | null
  error_message: string | null
  pod_name?: string | null
  pod_start_time?: string | null
  pod_phase?: string | null
}

export interface DeployProgressEvent {
  step: string
  message: string
  percentage: number
  engine_url?: string
}

// ── Auth ─────────────────────────────────────────────────

export async function login(
  username: string,
  password: string,
  captchaId: string,
  captchaAnswer: string
): Promise<AuthToken> {
  return api.post("/manager/auth/login", {
    username,
    password,
    captcha_id: captchaId,
    captcha_answer: captchaAnswer
  })
}

export interface CaptchaData {
  captcha_id: string
  image_base64: string
}

export async function getCaptcha(): Promise<CaptchaData> {
  return api.get("/manager/auth/captcha")
}

export async function getMe(): Promise<UserInfo> {
  return api.get("/manager/auth/me")
}

// ── 验证渠道查询 / 邮箱手机登录 / 忘记密码 ──

export interface VerificationChannels {
  email: boolean
  sms: boolean
}

export async function getVerificationChannels(): Promise<VerificationChannels> {
  return api.get("/manager/auth/verification-channels")
}

export interface ContactLoginPayload {
  contact_type: "email" | "phone"
  contact: string
  password: string
  captcha_id?: string
  captcha_answer?: string
}

export async function loginByContact(payload: ContactLoginPayload): Promise<AuthToken> {
  return api.post("/manager/auth/login-by-contact", payload)
}

export async function loginBySmsCode(phone: string, code: string): Promise<AuthToken> {
  return api.post("/manager/auth/login-by-sms-code", { phone, code })
}

export interface SendCodePayload {
  channel: "email" | "sms"
  target: string
  purpose: "login" | "reset_password"
  captcha_id: string
  captcha_answer: string
}

export interface SendCodeResult {
  sent: boolean
  expires_in: number
}

export async function sendVerificationCode(payload: SendCodePayload): Promise<SendCodeResult> {
  return api.post("/manager/auth/verification-code/send", payload)
}

export interface VerifyCodePayload {
  channel: "email" | "sms"
  target: string
  purpose: "login" | "reset_password"
  code: string
}

export interface VerifyCodeResult {
  ticket: string
}

export async function verifyVerificationCode(payload: VerifyCodePayload): Promise<VerifyCodeResult> {
  return api.post("/manager/auth/verification-code/verify", payload)
}

export async function resetPassword(ticket: string, newPassword: string): Promise<void> {
  return api.post("/manager/auth/reset-password", {
    ticket,
    new_password: newPassword,
  })
}

// ── Agents ───────────────────────────────────────────────

export async function getAccessibleAgents(): Promise<AccessibleAgent[]> {
  return api.get("/manager/agent-instances/accessible")
}

// ── 部署/模型（经 manager 代理，带组隔离 + 鉴权；不直调 controller）──

export async function getAgentStatus(agentId: string): Promise<AgentDeploymentStatus> {
  return api.get(`/manager/agent-instances/${agentId}/deployment-status`)
}

export async function deployAgent(agentId: string): Promise<AgentDeploymentStatus> {
  return api.post(`/manager/agent-instances/${agentId}/deploy`)
}

export interface AgentModelItem {
  id: string
  object: string
  provider?: string
}

export interface AgentModelsResponse {
  object: string
  data: AgentModelItem[]
}

export async function getAgentModels(agentId: string): Promise<AgentModelsResponse> {
  return api.get(`/manager/agent-instances/${agentId}/models`)
}

// ── 工作区文件浏览（只读；manager 经 k8s exec 读 profile 工作区）──

export interface WorkspaceFileEntry {
  name: string
  path: string
  is_dir: boolean
  size: number
  mtime_ns?: number
  is_text?: boolean
}

export interface WorkspaceFileList {
  entries: WorkspaceFileEntry[]
  path: string
  error?: string
}

export interface WorkspaceFileContent {
  path: string
  name: string
  size: number
  truncated: boolean
  is_text: boolean
  content: string | null
  content_b64: string | null
  is_image: boolean
  is_markdown: boolean
  max_bytes: number
  error?: string
}

export async function listAgentFiles(agentId: string, path: string = "."): Promise<WorkspaceFileList> {
  return api.get(`/manager/agent-instances/${agentId}/files?path=${encodeURIComponent(path)}`)
}

export async function readAgentFileContent(agentId: string, path: string): Promise<WorkspaceFileContent> {
  return api.get(`/manager/agent-instances/${agentId}/files/content?path=${encodeURIComponent(path)}`)
}

/**
 * 下载工作区文件（完整字节，无截断）——点击 agent 回复里的文件链接时用。
 * 因门户用 Bearer JWT 而非 cookie，裸 <a href> 带不上鉴权，必须 JS fetch 拿 blob 触发下载。
 * 从 Content-Disposition 还原中文文件名（manager 已按 RFC 5987 编码）。
 */
export async function downloadAgentFile(agentId: string, path: string): Promise<void> {
  const { getAccessToken, refreshAccessToken } = await import("./auth")
  const url = `/api/manager/agent-instances/${agentId}/files/download?path=${encodeURIComponent(path)}`
  const doFetch = (tok: string | null) =>
    fetch(url, tok ? { headers: { Authorization: `Bearer ${tok}` } } : {})
  let res = await doFetch(getAccessToken())
  if (res.status === 401) {
    const newToken = await refreshAccessToken()
    if (newToken) res = await doFetch(newToken)
  }
  if (!res.ok) {
    const err = await res.json().catch(() => ({ detail: res.statusText }))
    throw new Error(err.detail || `下载失败: ${res.status}`)
  }
  const blob = await res.blob()
  let filename = path.split("/").pop() || "download"
  const cd = res.headers.get("content-disposition") || ""
  const m = cd.match(/filename\*=UTF-8''([^;]+)/i)
  if (m) {
    try { filename = decodeURIComponent(m[1]) } catch { /* keep fallback */ }
  }
  const objUrl = URL.createObjectURL(blob)
  const a = document.createElement("a")
  a.href = objUrl
  a.download = filename
  document.body.appendChild(a)
  a.click()
  a.remove()
  URL.revokeObjectURL(objUrl)
}

// ── 消息反馈 / 收藏（对齐 Android MessageFeedbackRepository，manager 服务端持久化）──

export interface FeedbackUpsertPayload {
  agent_id: string
  session_id: string
  message_ref: string
  run_id?: string | null
  value?: "up" | "down" | null // null=取消
  reason?: "inaccurate" | "harmful" | "off_topic" | "other" | null // down 必填
  comment?: string | null
  content_snapshot?: string
}

export interface FeedbackItem {
  session_id?: string
  message_ref?: string
  run_id?: string | null
  value?: "up" | "down" | null
  reason?: string | null
  comment?: string | null
}

export interface FavoriteUpsertPayload {
  agent_id: string
  session_id: string
  message_ref: string
  run_id?: string | null
  content_snapshot?: string
}

export interface FavoriteDeletePayload {
  session_id: string
  message_ref: string
}

export interface FavoriteItem {
  id?: string
  agent_id?: string
  agent_name?: string
  session_id?: string
  message_ref?: string
  content_snapshot?: string | null
  created_at?: string | null
}

export async function upsertFeedback(payload: FeedbackUpsertPayload): Promise<FeedbackItem> {
  return api.put("/manager/message-feedback", payload)
}

export async function listFeedback(sessionId: string): Promise<FeedbackItem[]> {
  return api.get(`/manager/message-feedback?session_id=${encodeURIComponent(sessionId)}`)
}

export async function upsertFavorite(payload: FavoriteUpsertPayload): Promise<FavoriteItem> {
  return api.put("/manager/message-favorites", payload)
}

export async function deleteFavorite(payload: FavoriteDeletePayload): Promise<void> {
  return api.delete("/manager/message-favorites", payload)
}

export async function listSessionFavorites(sessionId: string): Promise<FavoriteItem[]> {
  return api.get(`/manager/message-favorites?session_id=${encodeURIComponent(sessionId)}`)
}
