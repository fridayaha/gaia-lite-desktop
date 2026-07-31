/**
 * 附件相关工具（迁自 enduser utils/attachment.ts）。
 *
 * 引擎是黑盒，会话历史由引擎自管——它收到的 user content（含 [Attached files: path]
 * 提示）会被原样存进历史。前端重载历史时拿到的 user 消息 content 仍带 hint，展示层需剥离。
 * 注意：只剥展示/标题，不改 content 本身（发给引擎的历史仍保留 hint，行为不变）。
 */

const ATTACHMENT_HINT_RE = /\n\n\[Attached files: [^\]]+\]$/;

/** 剥离 user 消息尾部的 [Attached files: path] 提示（仅用于展示/标题）。 */
export function stripAttachmentHint(content: string | undefined | null): string {
  if (typeof content !== "string") return "";
  return content.replace(ATTACHMENT_HINT_RE, "").trim();
}
