/**
 * Markdown 渲染——已迁入 @ua/chat 共享包，此处仅 re-export 保持现有 import 路径。
 * （prism-tomorrow / katex 的 CSS 副作用 import 随包 markdown 模块一并加载。）
 */
export { renderMarkdown, highlightCode } from "@ua/chat";
