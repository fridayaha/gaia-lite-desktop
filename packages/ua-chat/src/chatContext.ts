/**
 * 聊天上下文注入 —— 解耦宿主 API（@/api/*）。
 *
 * 宿主（enduser ChatPage / admin ChatView）在顶层 provide(chatContextKey, {...})，
 * 包内组件（ChatMessages / AuthenticatedImage / ChatFileBrowser）inject 使用。
 * 三个回调都是闭包，捕获宿主的 agentId，包内组件不再需要 agentId prop。
 */
import type { InjectionKey } from "vue";
import type { ImageResolver } from "./transport";

/** 工作区文件列表结果（对齐 manager listAgentFiles 返回）。 */
export interface FileListResult {
  entries: Array<{ name: string; path?: string; is_dir?: boolean; size?: number }>;
}

/** 工作区文件下载：传入相对路径，触发浏览器下载。 */
export type FileDownloader = (path: string) => Promise<void>;

/** 工作区文件列表：传入相对路径（"." 表根）。 */
export type FileLister = (path: string) => Promise<FileListResult>;

export interface ChatContext {
  /** 工作区图片按需解析（readAgentFileContent 闭包）。 */
  imageResolver?: ImageResolver;
  /** 工作区文件下载（downloadAgentFile 闭包）。 */
  fileDownloader?: FileDownloader;
  /** 工作区文件列表（listAgentFiles 闭包）。 */
  fileLister?: FileLister;
}

export const chatContextKey: InjectionKey<ChatContext> = Symbol("ua-chat-context");
