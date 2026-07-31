/**
 * 规范消息模型（平行模型）—— @ua/chat 共享组件消费的唯一消息形状。
 *
 * 取自 enduser useChat.ts 的 Message/Attachment 类型并泛化掉 hermes 专属字段。
 * 各 app 的 transport adapter 负责把后端事件归一成此模型：
 *   - enduser gateway 流已是平行模型（content + _toolCalls），透传即可
 *   - admin skill-engine 的 parts[] 有序模型由 SkillEngineTransport 展平成此模型
 *
 * 代价：admin 失去"文本→工具→文本"交错显示（统一 UX 的预期结果）。
 */

export type ChatRole = "user" | "assistant" | "system";

/** 用户上传/引擎引用的附件。 */
export interface Attachment {
  name: string;
  path: string;
  is_image: boolean;
  blobUrl?: string;
}

/** 单次工具调用（归一后）。各 adapter 把后端工具事件映射到此。 */
export interface ToolCall {
  id: string;
  toolName: string;
  args?: Record<string, unknown> | null;
  /** running | success | error */
  status: "running" | "success" | "error";
  result?: unknown;
  partialResult?: unknown;
  /** 原始错误信息（status=error 时） */
  error?: string;
}

/** 单条聊天消息（平行模型）。 */
export interface ChatMessage {
  id: string;
  role: ChatRole;
  content: string;
  isError?: boolean;
  /** 本回合用量（tokens 等），结构由 adapter 决定 */
  turnUsage?: unknown;
  model?: string;
  turnDuration?: number;
  ts?: number;
  providerDetails?: string;
  /** 工具调用列表（与 content 平行，无交错） */
  toolCalls?: ToolCall[];
  /** 思考过程文本（reasoning） */
  thinkingText?: string;
  retryable?: boolean;
  retryKind?: string;
  feedback?: "up" | "down";
  attachments?: Attachment[];
  /** 是否正在流式输出 */
  streaming?: boolean;
}

export type SessionStatus = "pending" | "responded" | "error";

/** 聊天会话。 */
export interface Session {
  id: string;
  title: string;
  messages: ChatMessage[];
  createdAt: number;
  lastMessageAt: number | null;
  model: string;
  /** 引擎侧会话 id（如 Dify conversation_id；hermes 由引擎自管） */
  engineConversationId?: string;
  status?: SessionStatus;
}
