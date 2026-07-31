/**
 * 富媒体后处理：Mermaid 图表 + JSON 树视图 + 工作区图片按需解析
 * 在 markdown 渲染 + Prism 高亮之后调用。
 *
 * 迁自 enduser utils/renderEnhancements.ts，解耦 readAgentFileContent → imageResolver 回调。
 * 宿主（enduser）传 imageResolver 闭包封装 agentId + readAgentFileContent；
 * 不传则跳过图片解析（admin Skill Studio 暂不解析工作区图片）。
 */

import type { ImageResolver } from "./transport";
import { isLocalImgSrc, normalizeWorkspacePath } from "./workspacePath";

let _mermaidLoaded: any = null;

async function loadMermaid(): Promise<any> {
  if (_mermaidLoaded) return _mermaidLoaded;
  const mod = await import("mermaid");
  _mermaidLoaded = mod.default;
  const isDark = document.documentElement.classList.contains("dark");
  _mermaidLoaded.initialize({
    startOnLoad: false,
    theme: isDark ? "dark" : "default",
    securityLevel: "loose"
  });
  return _mermaidLoaded;
}

let _mermaidIdCounter = 0;

/** 渲染容器内的 Mermaid 代码块为 SVG。 */
async function renderMermaidBlocks(container: HTMLElement): Promise<void> {
  const blocks = container.querySelectorAll<HTMLElement>("code.language-mermaid");
  if (blocks.length === 0) return;

  let mermaid: any;
  try {
    mermaid = await loadMermaid();
  } catch (e) {
    console.warn("[renderEnhancements] Failed to load mermaid:", e);
    return;
  }

  for (const codeEl of blocks) {
    if (codeEl.getAttribute("data-mermaid-rendered")) continue;
    codeEl.setAttribute("data-mermaid-rendered", "1");

    const code = codeEl.textContent || "";
    const codeBlock = codeEl.closest(".code-block");
    const id = `mermaid-svg-${_mermaidIdCounter++}`;

    try {
      const { svg } = await mermaid.render(id, code);
      const wrapper = document.createElement("div");
      wrapper.className = "mermaid-rendered";
      wrapper.innerHTML = svg;
      if (codeBlock) {
        codeBlock.parentNode!.insertBefore(wrapper, codeBlock);
        codeBlock.remove();
      } else {
        codeEl.parentNode!.replaceChild(wrapper, codeEl);
      }
    } catch (e) {
      console.warn("[renderEnhancements] Mermaid render failed:", e);
    }
  }
}

/** 为长 JSON 代码块构建可折叠树视图。 */
function initJsonTreeViews(container: HTMLElement): void {
  const blocks = container.querySelectorAll<HTMLElement>("code.language-json");
  for (const codeEl of blocks) {
    if (codeEl.getAttribute("data-tree-init")) continue;
    const text = codeEl.textContent || "";
    const lineCount = text.split("\n").length;
    if (lineCount < 10) continue;

    let parsed: any;
    try {
      parsed = JSON.parse(text);
    } catch {
      continue;
    }
    if (typeof parsed !== "object" || parsed === null) continue;

    codeEl.setAttribute("data-tree-init", "1");
    const codeBlock = codeEl.closest(".code-block");
    if (!codeBlock) continue;

    const treeHtml = buildJsonTree(parsed, true);
    const treeWrap = document.createElement("div");
    treeWrap.className = "json-tree-wrap";
    treeWrap.innerHTML = `
      <div class="json-tree-toolbar">
        <button class="json-tree-toggle" type="button">树形视图</button>
      </div>
      <div class="json-tree-body">${treeHtml}</div>
    `;

    const toggleBtn = treeWrap.querySelector(".json-tree-toggle") as HTMLButtonElement;
    const treeBody = treeWrap.querySelector(".json-tree-body") as HTMLElement;
    let showTree = true;

    toggleBtn.addEventListener("click", () => {
      showTree = !showTree;
      if (showTree) {
        treeBody.style.display = "";
        (codeBlock as HTMLElement).style.display = "none";
        toggleBtn.textContent = "树形视图";
      } else {
        treeBody.style.display = "none";
        (codeBlock as HTMLElement).style.display = "";
        toggleBtn.textContent = "原始视图";
      }
    });

    (codeBlock as HTMLElement).style.display = "none";
    codeBlock.parentNode!.insertBefore(treeWrap, codeBlock);
  }
}

function buildJsonTree(data: any, isRoot: boolean = false): string {
  if (data === null) return '<span class="json-null">null</span>';
  if (typeof data === "boolean") return `<span class="json-bool">${data}</span>`;
  if (typeof data === "number") return `<span class="json-num">${data}</span>`;
  if (typeof data === "string") return `<span class="json-str">"${escapeJsonStr(data)}"</span>`;

  const entries = Array.isArray(data)
    ? data.map((v, i) => [String(i), v] as [string, any])
    : Object.entries(data);

  if (entries.length === 0) {
    return Array.isArray(data) ? "[]" : "{}";
  }

  const items = entries.map(([key, val]) => {
    const isExpandable = val !== null && typeof val === "object";
    const keyClass = Array.isArray(data) ? "json-key-idx" : "json-key";
    const keyHtml = `<span class="${keyClass}">${escapeJsonStr(key)}</span>`;
    if (isExpandable) {
      return `<details><summary>${keyHtml}: <span class="json-preview">${jsonPreview(val)}</span></summary><div class="json-tree-children">${buildJsonTree(val)}</div></details>`;
    }
    return `<div class="json-tree-leaf">${keyHtml}: ${buildJsonTree(val)}</div>`;
  }).join("");

  if (isRoot) {
    return `<details open class="json-tree-root"><summary>{${entries.length}}</summary><div class="json-tree-children">${items}</div></details>`;
  }
  return items;
}

function jsonPreview(val: any): string {
  if (Array.isArray(val)) return `[${val.length}]`;
  if (val !== null && typeof val === "object") return `{${Object.keys(val).length}}`;
  return "";
}

function escapeJsonStr(s: string): string {
  return s.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
}

function guessImageMime(path: string): string {
  const ext = (path.toLowerCase().split(".").pop() || "").trim();
  const map: Record<string, string> = {
    png: "image/png", jpg: "image/jpeg", jpeg: "image/jpeg", gif: "image/gif",
    svg: "image/svg+xml", webp: "image/webp", ico: "image/x-icon", bmp: "image/bmp"
  };
  return map[ext] || "image/png";
}

/**
 * 把容器内引用工作区本地路径的 <img> 按需解析为 data URL（调宿主注入的 imageResolver）。
 * 不在消息内容里内联 base64 —— 否则 base64 会进会话历史，每轮回传 LLM 浪费 token。
 */
async function resolveAgentImages(container: HTMLElement, imageResolver?: ImageResolver): Promise<void> {
  if (!imageResolver) return;
  const imgs = container.querySelectorAll<HTMLImageElement>("img.agent-img-pending, img:not(.msg-media-img)");
  for (const img of imgs) {
    const dataPath = img.getAttribute("data-path");
    const srcAttr = img.getAttribute("src");
    const rawRef = dataPath || (isLocalImgSrc(srcAttr) ? (srcAttr as string) : "");
    const ref = normalizeWorkspacePath(rawRef);
    if (!ref) continue;
    if (img.getAttribute("data-img-resolved")) continue;
    img.setAttribute("data-img-resolved", "1");
    try {
      const data = await imageResolver(ref);
      if (data?.is_image && data.content_b64) {
        img.src = `data:${guessImageMime(ref)};base64,${data.content_b64}`;
        img.classList.remove("agent-img-pending");
        img.classList.add("msg-media-img");
      } else {
        img.classList.remove("agent-img-pending");
      }
    } catch {
      img.classList.remove("agent-img-pending");
    }
  }
}

/**
 * 主入口：在 markdown 渲染 + Prism 高亮后调用。
 * 处理 工作区图片按需解析（需 imageResolver）+ Mermaid + JSON 树 + 图片样式。
 */
export async function enhanceRendered(
  container: HTMLElement | null,
  imageResolver?: ImageResolver
): Promise<void> {
  if (!container) return;
  await resolveAgentImages(container, imageResolver);
  container.querySelectorAll("img:not(.msg-media-img)").forEach((img) => {
    img.classList.add("msg-media-img");
  });
  initJsonTreeViews(container);
  await renderMermaidBlocks(container);
}
