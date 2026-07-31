/**
 * @ua/chat — UnionAgents 共享对话区组件包。
 *
 * 单 UI 栈，跨 admin（Skill Studio）/ enduser（门户）复用。
 * 消费方通过 vite alias + tsconfig paths 把 `@ua/chat` 指向本 src/index.ts。
 *
 * Phase 1：规范类型 + 传输/文件接口 + 纯逻辑（markdown 管线 / toolCallRender /
 * attachment / workspacePath / renderEnhancements）。组件迁入在 Phase 2+。
 */

export const UA_CHAT_VERSION = "0.1.0";

// 规范类型
export type {
  ChatRole,
  Attachment,
  ToolCall,
  ChatMessage,
  Session,
  SessionStatus
} from "./types";

// 传输 / 文件接口
export type {
  ChatEvent,
  ChatTransport,
  ResolvedImage,
  ImageResolver,
  FileApi,
  FileInfo,
  FileRead,
  ChatTheme
} from "./transport";

// 纯渲染辅助（迁自 admin skill-studio）
export {
  extractResultText,
  extractEditDiff,
  toolSummary,
  isClarifyTool,
  diffLineKind,
  editsToDiffLines,
  isFileMutationTool
} from "./toolCallRender";
export type { ToolResult, DiffLineKind } from "./toolCallRender";

// 附件工具（迁自 enduser）
export { stripAttachmentHint } from "./attachment";

// 工作区路径归一化（迁自 enduser）
export { isLocalImgSrc, normalizeWorkspacePath } from "./workspacePath";

// markdown 管线（迁自 enduser）
export { renderMarkdown, highlightCode } from "./markdown";

// 富媒体后处理（迁自 enduser，imageResolver 解耦）
export { enhanceRendered } from "./renderEnhancements";

// 图标（迁自 enduser，自维护 SVG path 字典，不依赖 lucide 包）
export { LUCIDE_PATHS, fileIconName } from "./icons/lucide";
export { default as LucideIcon } from "./icons/LucideIcon.vue";

// 聊天展示组件（迁自 enduser，Phase 2a：零/低耦合叶子组件）
export { default as StatusCard } from "./components/StatusCard.vue";
export { default as ToolCard } from "./components/ToolCard.vue";
export { default as ImageLightbox } from "./components/ImageLightbox.vue";
export { default as BottomTabbar } from "./components/BottomTabbar.vue";
export { default as ChatSessionList } from "./components/ChatSessionList.vue";
export { default as ChatComposer } from "./components/ChatComposer.vue";
export { default as ThinkingCard } from "./components/ThinkingCard.vue";
export { default as ApprovalCard } from "./components/ApprovalCard.vue";
export { default as AuthenticatedImage } from "./components/AuthenticatedImage.vue";
export { default as ChatFileBrowser } from "./components/ChatFileBrowser.vue";
export { default as FileBrowser } from "./components/FileBrowser.vue";
export { default as ChatMessages } from "./components/ChatMessages.vue";

// 聊天上下文注入（解耦宿主 API：imageResolver / fileDownloader / fileLister）
export { chatContextKey } from "./chatContext";
export type { ChatContext, FileListResult, FileDownloader, FileLister } from "./chatContext";
