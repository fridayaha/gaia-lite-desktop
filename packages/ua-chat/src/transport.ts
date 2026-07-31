/**
 * 传输 / 文件 adapter 接口 —— @ua/chat 组件只消费这些接口，不 import 任何宿主 `@/`。
 *
 * 各 app 实现自己的 adapter：
 *   - enduser：GatewayTransport（hermes/openai 双协议）+ enduser FileApi（只读，无 write）
 *   - admin：SkillEngineTransport（parts[]→平行模型）+ admin FileApi（可写）
 *
 * 后端 SSE 协议不收敛，靠 adapter 归一成规范 ChatMessage。
 */

import type { Attachment, ChatMessage, Session } from "./types";

/** adapter 向外抛的事件（已归一为规范模型）。 */
export type ChatEvent =
  | { type: "session"; session: Session }
  | { type: "message_start"; message: ChatMessage }
  | { type: "message_delta"; messageId: string; contentDelta?: string; thinkingDelta?: string }
  | { type: "message_end"; messageId: string; message: ChatMessage }
  | { type: "tool_update"; messageId: string; toolCall: import("./types").ToolCall }
  | { type: "error"; message: string; retryable?: boolean }
  | { type: "usage"; messageId: string; usage: unknown; model?: string; durationMs?: number }
  | { type: "done" };

/**
 * 传输 adapter：负责发起一轮对话并把后端 SSE 流归一成 ChatEvent。
 * 实现方持有鉴权、URL 构造、协议解析细节。
 */
export interface ChatTransport {
  /** 发起一轮对话（prompt + 可选附件）。resolve 于流正常开启，不代表流结束。 */
  start(prompt: string, attachments?: Attachment[]): Promise<void>;
  /** 中止当前流。 */
  abort(): void;
  /** 订阅归一后的事件。返回取消订阅函数。 */
  onEvent(cb: (e: ChatEvent) => void): () => void;
  /** 释放资源（断开 SSE、清状态）。 */
  dispose(): void;
}

/** 工作区图片按需解析结果（renderEnhancements 用）。 */
export interface ResolvedImage {
  is_image: boolean;
  content_b64: string;
}

/** 把工作区相对路径解析成 base64 图片数据的回调（解耦宿主 API）。 */
export type ImageResolver = (ref: string) => Promise<ResolvedImage | null>;

/**
 * 文件 API 接口。list/read/download 必填；write 可选（决定编辑 UI 显隐）。
 *   - admin：实现 write（可编辑 FileBrowser）
 *   - enduser：不实现 write（只读 CodeMirror 高亮）
 */
export interface FileApi {
  list(path: string): Promise<FileInfo[]>;
  read(path: string): Promise<FileRead>;
  download(path: string): Promise<void>;
  write?(path: string, content: string): Promise<void>;
}

export interface FileInfo {
  name: string;
  path: string;
  isDir: boolean;
  size?: number;
}

export interface FileRead {
  path: string;
  content: string;
  isBinary: boolean;
  size?: number;
  /** 图片预览（enduser 工作区可能有图片，admin 无）。 */
  isImage?: boolean;
  contentB64?: string;
  /** 文本被截断（超过后端 max_bytes）。 */
  truncated?: boolean;
}

/** 主题 token 注入（CSS 变量覆盖）。 */
export interface ChatTheme {
  /** 覆盖默认 CSS 变量，如 { "ua-chat-accent": "#6d5efc" }。 */
  tokens?: Record<string, string>;
}
