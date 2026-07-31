/**
 * toolCallRender.ts — 工具调用卡片的纯渲染辅助函数（迁自 admin skill-studio）。
 *
 * 事件来源（pi SDK，经 engine-worker 原样转发）：
 *   tool_execution_start  { toolCallId, toolName, args }
 *   tool_execution_update { toolCallId, toolName, args, partialResult }
 *   tool_execution_end    { toolCallId, toolName, result, isError }
 *
 * result 是 AgentToolResult：{ content: (TextContent|ImageContent)[], details, ... }
 * - bash/grep/read 的输出文本在 result.content[]
 * - edit 的展示 diff 在 result.details.diff（unified diff 字符串）
 *
 * 这些函数保持纯：无副作用、可单测，被 ToolCard 与 detail 容器复用。
 */

/** AgentToolResult.content 中的文本块。 */
interface TextContentBlock {
  type: string;
  text?: string;
}

/** AgentToolResult 形状（只取渲染关心的字段）。 */
export interface ToolResult {
  content?: TextContentBlock[] | unknown[];
  details?: Record<string, unknown> | null;
  isError?: boolean;
}

/** 从 result.content[] 拼接所有文本块的 text，供 bash/grep/read 复用。 */
export function extractResultText(result: unknown): string {
  if (!result || typeof result !== "object") return "";
  const content = (result as { content?: unknown }).content;
  if (!Array.isArray(content)) return "";
  return content
    .map((b) => (b && typeof b === "object" && "text" in b ? String((b as TextContentBlock).text ?? "") : ""))
    .filter((t) => t.length > 0)
    .join("");
}

/** edit 工具 result.details.diff（展示用 unified diff 字符串）。 */
export function extractEditDiff(result: unknown): string {
  if (!result || typeof result !== "object") return "";
  const details = (result as { details?: unknown }).details;
  if (details && typeof details === "object" && "diff" in details) {
    const diff = (details as { diff?: unknown }).diff;
    return typeof diff === "string" ? diff : "";
  }
  return "";
}

/** 给定 toolName + args，返回卡片头部摘要（路径/命令/pattern）。 */
export function toolSummary(
  toolName: string,
  args: Record<string, unknown> | null
): string {
  if (!args) return toolName;
  switch (toolName) {
    case "write":
    case "edit":
    case "read":
    case "ls":
      return typeof args.path === "string" ? args.path : toolName;
    case "bash":
      return typeof args.command === "string" ? args.command : toolName;
    case "grep":
      return typeof args.pattern === "string" ? args.pattern : toolName;
    case "clarify":
      return typeof args.title === "string" && args.title ? args.title : "clarify";
    default:
      return toolName;
  }
}

/** 判断是否为 clarify 工具（需求澄清问卷，前端渲染为可填写表单）。 */
export function isClarifyTool(toolName: string): boolean {
  return toolName === "clarify";
}

/** 一行 unified diff 的渲染分类。 */
export type DiffLineKind = "add" | "del" | "hunk" | "context";

export function diffLineKind(line: string): DiffLineKind {
  if (line.startsWith("+++") || line.startsWith("---")) return "hunk";
  if (line.startsWith("@@")) return "hunk";
  if (line.startsWith("+")) return "add";
  if (line.startsWith("-")) return "del";
  return "context";
}

/**
 * edit 工具无 result.details.diff 时，用 args.edits 渲染简易 old→new 块。
 * 返回形如 `-{oldText}` / `+{newText}` 的行数组，复用 diff 着色。
 */
export function editsToDiffLines(edits: unknown): string[] {
  if (!Array.isArray(edits)) return [];
  const lines: string[] = [];
  for (const e of edits) {
    if (!e || typeof e !== "object") continue;
    const oldText = String((e as { oldText?: unknown }).oldText ?? "");
    const newText = String((e as { newText?: unknown }).newText ?? "");
    for (const l of oldText.split("\n")) lines.push("-" + l);
    for (const l of newText.split("\n")) lines.push("+" + l);
  }
  return lines;
}

/** 判断该工具是否可能改动工作区文件（用于联动 FileBrowser 刷新）。 */
export function isFileMutationTool(toolName: string): boolean {
  return toolName === "write" || toolName === "edit";
}
