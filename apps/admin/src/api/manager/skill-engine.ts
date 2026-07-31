/**
 * Skill Engine API — 技能开发/调试引擎，经 Manager 代理 /api/skill-engine/*。
 *
 * Manager 代理注入 X-Actor-Id/X-Group-Id/X-Roles 身份头，前端只需带 admin JWT
 *（http 拦截器自动加）。SSE 事件流不走本模块（axios 不支持流式），由
 * useEngineSession composable 用裸 fetch + ReadableStream 解析。
 */
import { http } from "@/utils/http";

// ── 类型 ────────────────────────────────────────────────

export type EngineRole = "dev" | "debug";

export type Workspace = {
  id: string;
  name: string;
  description: string;
  localPath?: string;
  status: string;
  createdAt?: string;
  updatedAt?: string;
};

export type WorkspaceListResponse = {
  workspaces: Workspace[];
};

export type FileEntry = {
  path: string; // posix 相对路径
  size: number; // 文件字节数，目录为 0
  isDir: boolean;
  modifiedAt: string; // ISO 时间戳
};

export type FileTreeResponse = {
  files: FileEntry[];
};

/** 文件读取结果。后端返回 isText，前端适配为 isBinary。 */
export type FileRead = {
  content: string;
  isBinary: boolean;
  size: number;
  path: string;
};

export type EngineMessage = {
  id: string;
  role: string;
  seq: number;
  sender: "user" | "assistant" | "system" | string;
  content: string;
  toolCalls: Record<string, unknown>[] | null;
  createdAt: string;
};

export type MessageHistoryResponse = {
  messages: EngineMessage[];
};

export type SessionInfo = {
  sessionId: string; // = role（"dev" | "debug"）
  role: EngineRole;
  status: "ready" | "starting" | string;
};

export type InstanceState = {
  isStreaming: boolean;
  model: { provider: string; modelId: string } | null;
  messageCount: number;
};

// ── Workspace CRUD ─────────────────────────────────────

export const listWorkspacesApi = () => {
  return http.request<WorkspaceListResponse>("get", "/api/skill-engine/workspaces");
};

export const createWorkspaceApi = (data: { name: string; description?: string }) => {
  return http.request<Workspace>("post", "/api/skill-engine/workspaces", { data });
};

export const getWorkspaceApi = (id: string) => {
  return http.request<Workspace>("get", `/api/skill-engine/workspaces/${id}`);
};

export const deleteWorkspaceApi = (id: string) => {
  return http.request<{ ok: boolean }>("delete", `/api/skill-engine/workspaces/${id}`);
};

// ── 文件操作 ─────────────────────────────────────────────

export const listFilesApi = (id: string) => {
  return http.request<FileTreeResponse>("get", `/api/skill-engine/workspaces/${id}/files`);
};

export const readFileApi = async (id: string, path: string): Promise<FileRead> => {
  const res = await http.request<{
    content: string | ArrayBuffer;
    isText: boolean;
    size: number;
    path: string;
  }>("get", `/api/skill-engine/workspaces/${id}/files/${path}`);
  // 后端 isText → 前端 isBinary（取反），文本内容统一成 string
  return {
    content: typeof res.content === "string" ? res.content : "",
    isBinary: !res.isText,
    size: res.size,
    path: res.path,
  };
};

export const writeFileApi = (id: string, path: string, content: string) => {
  return http.request<{ ok: boolean; path: string; size: number }>(
    "put",
    `/api/skill-engine/workspaces/${id}/files/${path}`,
    { data: { content } }
  );
};

// ── config_params（密钥/配置）───────────────────────────────────

/** config_params 状态：非密钥值（明文）+ 密钥已配置的 key 列表（不返明文）。 */
export type WorkspaceConfigStatus = {
  configValues: Record<string, unknown>;
  configured: string[];
};

/** 读取工作区 config_params 状态。密钥不返明文，仅返已配置 key 列表。 */
export const getConfigApi = (workspaceId: string) => {
  return http.request<WorkspaceConfigStatus>(
    "get",
    `/api/skill-engine/workspaces/${workspaceId}/config`
  );
};

/**
 * 保存 config_params 值。
 * - config: 非密钥值（明文，type 校验）
 * - credentials: 密钥值（加密存储；空值 = 不改）
 */
export const saveConfigApi = (
  workspaceId: string,
  data: { config?: Record<string, unknown>; credentials?: Record<string, string> },
) => {
  return http.request<{ ok: boolean; configured: string[]; configValues: Record<string, unknown> }>(
    "put",
    `/api/skill-engine/workspaces/${workspaceId}/config`,
    { data }
  );
};

/**
 * 以 base64 读取工作区文件（供对话区 imageResolver 渲染技能产出的图片）。
 * 对齐 @ua/chat 的 ImageResolver 签名：返回 { is_image, content_b64 }。
 * 非图片或读取失败返回 null（让共享包跳过该 img，不留占位）。
 */
export const readFileAsImageApi = async (
  id: string,
  path: string,
): Promise<{ is_image: boolean; content_b64: string } | null> => {
  try {
    const res = await http.request<{
      contentB64: string;
      isImage: boolean;
      mime: string;
      size: number;
      path: string;
    }>("get", `/api/skill-engine/workspaces/${id}/files/${path}`, {
      params: { base64: 1 }
    });
    if (!res.isImage) return null;
    return { is_image: true, content_b64: res.contentB64 };
  } catch {
    return null;
  }
};

// ── 引擎会话 ─────────────────────────────────────────────

export const startSessionApi = (workspaceId: string, role: EngineRole) => {
  return http.request<SessionInfo>("post", `/api/skill-engine/workspaces/${workspaceId}/sessions`, {
    data: { role }
  });
};

export const stopSessionApi = (workspaceId: string, sid: string) => {
  return http.request<{ ok: boolean }>(
    "delete",
    `/api/skill-engine/workspaces/${workspaceId}/sessions/${sid}`
  );
};

export const promptApi = (workspaceId: string, sid: string, message: string) => {
  return http.request<any>("post", `/api/skill-engine/workspaces/${workspaceId}/sessions/${sid}/prompt`, {
    data: { message }
  });
};

export const steerApi = (workspaceId: string, sid: string, message: string) => {
  return http.request<any>("post", `/api/skill-engine/workspaces/${workspaceId}/sessions/${sid}/steer`, {
    data: { message }
  });
};

export const followUpApi = (workspaceId: string, sid: string, message: string) => {
  return http.request<any>(
    "post",
    `/api/skill-engine/workspaces/${workspaceId}/sessions/${sid}/follow-up`,
    { data: { message } }
  );
};

export const abortApi = (workspaceId: string, sid: string) => {
  return http.request<any>("post", `/api/skill-engine/workspaces/${workspaceId}/sessions/${sid}/abort`);
};

export const reloadApi = (workspaceId: string, sid: string) => {
  return http.request<any>("post", `/api/skill-engine/workspaces/${workspaceId}/sessions/${sid}/reload`);
};

/**
 * 提交 clarify 工具的用户答案，解析阻塞中的需求澄清问卷。
 * answers 按 question id 索引：text/single→string，multi→string[]，confirm→boolean。
 */
export const submitClarifyApi = (
  workspaceId: string,
  sid: string,
  toolCallId: string,
  answers: Record<string, unknown>,
) => {
  return http.request<{ ok: boolean; error?: string }>(
    "post",
    `/api/skill-engine/workspaces/${workspaceId}/sessions/${sid}/tools/${toolCallId}/response`,
    { data: { answers } }
  );
};

export const getStateApi = (workspaceId: string, sid: string) => {
  return http.request<InstanceState>(
    "get",
    `/api/skill-engine/workspaces/${workspaceId}/sessions/${sid}/state`
  );
};

/** 清除某会话的全部消息（dev/debug 独立）：删 messages 表 + Redis 缓存。 */
export const clearSessionMessagesApi = (workspaceId: string, sid: string) => {
  return http.request<{ ok: boolean; deleted: number }>(
    "delete",
    `/api/skill-engine/workspaces/${workspaceId}/sessions/${sid}/messages`
  );
};

export const listMessagesApi = (workspaceId: string, sid: string) => {
  return http.request<MessageHistoryResponse>(
    "get",
    `/api/skill-engine/workspaces/${workspaceId}/sessions/${sid}/messages`
  );
};

// ── 验证 / 打包 / 发布 ──────────────────────────────────

export type ValidateResult = {
  valid: boolean;
  errors: Array<{ field: string; message: string }>;
  manifest?: Record<string, unknown>;
};

export type PublishResult = {
  itemId: string;
  versionId: string;
  scan: {
    riskLevel: string;
    findingsCount: number;
    findings: Array<Record<string, unknown>>;
  } | null;
  warnings: Array<Record<string, unknown>>;
};

export type ScanResult = {
  riskLevel: string;
  findingsCount: number;
  findings: Array<Record<string, unknown>>;
  summary: Record<string, unknown>;
};

export const validateApi = (workspaceId: string) => {
  return http.request<ValidateResult>(
    "post",
    `/api/skill-engine/workspaces/${workspaceId}/validate`
  );
};

export const publishApi = (workspaceId: string) => {
  return http.request<PublishResult>(
    "post",
    `/api/skill-engine/workspaces/${workspaceId}/publish`
  );
};

export const scanApi = (workspaceId: string) => {
  return http.request<ScanResult>(
    "post",
    `/api/skill-engine/workspaces/${workspaceId}/scan`
  );
};

/**
 * Download the built skill package. Returns a Blob (the route streams a zip).
 * Authenticated via the same JWT as other requests.
 */
export const downloadPackageApi = async (workspaceId: string): Promise<Blob> => {
  return http.request<Blob>("get", `/api/skill-engine/workspaces/${workspaceId}/build-package`, {
    responseType: "blob"
  });
};
