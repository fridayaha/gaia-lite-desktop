/**
 * Markdown 渲染模块（settled 消息）—— 迁自 enduser utils/markdown.ts。
 * 使用 marked + DOMPurify + Prism.js + KaTeX。
 *
 * CSS 副作用 import（prism-tomorrow / katex.min.css）随包打包，
 * 宿主无需再单独引入；样式类名走 chat.css 的 .code-block / .agent-img-pending 等。
 */
import { marked } from "marked";
import DOMPurify from "dompurify";
import Prism from "prismjs";
import "prismjs/themes/prism-tomorrow.css";
import katex from "katex";
import "katex/dist/katex.min.css";

import { normalizeWorkspacePath } from "./workspacePath";

// 常用语言（按需引入，控制包体积）
import "prismjs/components/prism-python";
import "prismjs/components/prism-javascript";
import "prismjs/components/prism-typescript";
import "prismjs/components/prism-jsx";
import "prismjs/components/prism-tsx";
import "prismjs/components/prism-json";
import "prismjs/components/prism-yaml";
import "prismjs/components/prism-bash";
import "prismjs/components/prism-go";
import "prismjs/components/prism-rust";
import "prismjs/components/prism-sql";
import "prismjs/components/prism-java";
import "prismjs/components/prism-c";
import "prismjs/components/prism-cpp";
import "prismjs/components/prism-csharp";
import "prismjs/components/prism-diff";
import "prismjs/components/prism-markdown";
import "prismjs/components/prism-css";

// ── marked 配置 ──
marked.setOptions({ gfm: true, breaks: true });

// 自定义 renderer：给代码块包 .code-block > .code-block-header（语言标签 + 复制按钮）
const renderer = new marked.Renderer();

renderer.code = function (token: { text: string; lang?: string }) {
  const lang = (token.lang || "").trim();
  const langClass = lang ? ` class="language-${lang}"` : "";
  const escaped = escapeHtml(token.text);
  return `<div class="code-block"><div class="code-block-header"><span class="code-lang">${lang}</span><button class="code-copy-btn" type="button">复制</button></div><pre><code${langClass}>${escaped}</code></pre></div>`;
};

/**
 * 本地工作区图片路径（非 http/data/blob）——不内联 base64，留 data-path 给前端按需解析。
 * 否则 base64 会进会话历史，每轮回传 LLM 浪费大量 token。
 */
renderer.image = function (token: { href: string; title: string | null; text: string }) {
  const href = (token.href || "").trim();
  const alt = token.text || "";
  const title = token.title ? ` title="${escapeHtml(token.title)}"` : "";
  if (/^(https?:|data:|blob:)/i.test(href)) {
    return `<img src="${escapeHtml(href)}" alt="${escapeHtml(alt)}"${title} />`;
  }
  return `<img class="agent-img-pending" data-path="${escapeHtml(normalizeWorkspacePath(href))}" alt="${escapeHtml(alt)}"${title} />`;
};

marked.use({
  renderer,
  tokenizer: {
    // 禁用 GFM 删除线（del）：marked v18 的 del 正则 ~~? 会把单个 ~ 当作删除线起点，
    // 误吞 `cd ~/projects`、`int y = ~x;` 这类文本。禁用 tokenizer 让 ~ 保持字面量，
    // 代码块与正文都正确（代价：~~strikethrough~~ 不再渲染为删除线）。
    del() {
      return undefined;
    }
  }
});

// 链接安全：通过 DOMPurify hook 强制 target=_blank rel=noopener
DOMPurify.addHook("afterSanitizeAttributes", (node: Element) => {
  if (node.tagName === "A") {
    node.setAttribute("target", "_blank");
    node.setAttribute("rel", "noopener noreferrer");
  }
});

function escapeHtml(s: string): string {
  return s
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;");
}

// ── DOMPurify 配置 ──
const PURIFY_CONFIG = {
  ALLOWED_TAGS: [
    "p", "br", "strong", "em", "del", "code", "pre", "div", "span",
    "h1", "h2", "h3", "h4", "h5", "h6",
    "ul", "ol", "li", "input",
    "table", "thead", "tbody", "tr", "th", "td",
    "a", "img", "blockquote", "hr",
    "details", "summary"
  ],
  ALLOWED_ATTR: [
    "href", "title", "src", "alt", "class", "target", "rel",
    "type", "checked", "disabled"
  ],
  ALLOW_DATA_ATTR: false,
  ADD_ATTR: ["data-path"]
};

// ── KaTeX 数学公式 stash ──
interface MathEntry { tex: string; display: boolean }

function stashMath(text: string): { text: string; stash: MathEntry[] } {
  const stash: MathEntry[] = [];
  text = text.replace(/\$\$([\s\S]+?)\$\$|\\\[([\s\S]+?)\\\]/g, (_m, a, b) => {
    stash.push({ tex: a || b, display: true });
    return `KATEXBLOCK${stash.length - 1}ENDKATEX`;
  });
  text = text.replace(/\$([^\$\n]+?)\$|\\\((.+?)\\\)/g, (_m, a, b) => {
    stash.push({ tex: a || b, display: false });
    return `KATEXINLINE${stash.length - 1}ENDKATEX`;
  });
  return { text, stash };
}

function restoreMath(html: string, stash: MathEntry[]): string {
  if (stash.length === 0) return html;
  html = html.replace(/KATEXBLOCK(\d+)ENDKATEX/g, (_m, i) => {
    const entry = stash[parseInt(i)];
    try {
      return katex.renderToString(entry.tex, { throwOnError: false, displayMode: true });
    } catch {
      return escapeHtml(entry.tex);
    }
  });
  html = html.replace(/KATEXINLINE(\d+)ENDKATEX/g, (_m, i) => {
    const entry = stash[parseInt(i)];
    try {
      return katex.renderToString(entry.tex, { throwOnError: false, displayMode: false });
    } catch {
      return escapeHtml(entry.tex);
    }
  });
  return html;
}

/**
 * 渲染 markdown 文本为安全 HTML（用于 settled 消息）。
 */
export function renderMarkdown(text: string): string {
  if (!text) return "";
  try {
    const { text: stashed, stash } = stashMath(text);
    const raw = marked.parse(stashed, { async: false }) as string;
    const sanitized = DOMPurify.sanitize(raw, PURIFY_CONFIG) as string;
    return restoreMath(sanitized, stash);
  } catch (e) {
    console.error("[markdown] renderMarkdown error:", e);
    return escapeHtml(text);
  }
}

/**
 * 对容器内未高亮的代码块执行 Prism 语法高亮。
 */
export function highlightCode(container: HTMLElement | null): void {
  if (!container) return;
  const blocks = container.querySelectorAll<HTMLElement>("pre code:not([data-highlighted])");
  for (const el of blocks) {
    const langClass = el.className.match(/language-([\w-]+)/);
    const lang = langClass?.[1] || "";
    if (lang === "mermaid") continue;
    try {
      Prism.highlightElement(el);
    } catch {
      // 未知语言时 Prism 静默失败
    }
    el.setAttribute("data-highlighted", "1");
  }
}
