/**
 * 纯函数单测 —— toolCallRender / attachment / workspacePath。
 * renderMarkdown 依赖 DOM（DOMPurify），留待 jsdom 环境补充。
 */
import { describe, it, expect } from "vitest";
import {
  extractResultText,
  extractEditDiff,
  toolSummary,
  isClarifyTool,
  diffLineKind,
  editsToDiffLines,
  isFileMutationTool
} from "../toolCallRender";
import { stripAttachmentHint } from "../attachment";
import { isLocalImgSrc, normalizeWorkspacePath } from "../workspacePath";

describe("toolCallRender", () => {
  describe("extractResultText", () => {
    it("拼接 content[] 所有文本块的 text", () => {
      const result = { content: [{ type: "text", text: "hello " }, { type: "text", text: "world" }] };
      expect(extractResultText(result)).toBe("hello world");
    });
    it("跳过无 text 字段的块", () => {
      const result = { content: [{ type: "image" }, { type: "text", text: "ok" }] };
      expect(extractResultText(result)).toBe("ok");
    });
    it("空字符串块被过滤", () => {
      const result = { content: [{ type: "text", text: "" }, { type: "text", text: "x" }] };
      expect(extractResultText(result)).toBe("x");
    });
    it("非对象/无 content 返回空串", () => {
      expect(extractResultText(null)).toBe("");
      expect(extractResultText(undefined)).toBe("");
      expect(extractResultText("string")).toBe("");
      expect(extractResultText({})).toBe("");
      expect(extractResultText({ content: "not-array" })).toBe("");
    });
  });

  describe("extractEditDiff", () => {
    it("取 details.diff 字符串", () => {
      expect(extractEditDiff({ details: { diff: "--- a\n+++ b\n@@ -1 +1 @@" } })).toBe("--- a\n+++ b\n@@ -1 +1 @@");
    });
    it("diff 非字符串返回空", () => {
      expect(extractEditDiff({ details: { diff: 123 } })).toBe("");
    });
    it("无 details 返回空", () => {
      expect(extractEditDiff({})).toBe("");
      expect(extractEditDiff(null)).toBe("");
    });
  });

  describe("toolSummary", () => {
    it("write/edit/read/ls 取 args.path", () => {
      expect(toolSummary("write", { path: "SKILL.md" })).toBe("SKILL.md");
      expect(toolSummary("edit", { path: "a.ts" })).toBe("a.ts");
      expect(toolSummary("read", { path: "b.json" })).toBe("b.json");
      expect(toolSummary("ls", { path: "src/" })).toBe("src/");
    });
    it("bash 取 args.command", () => {
      expect(toolSummary("bash", { command: "ls -la" })).toBe("ls -la");
    });
    it("grep 取 args.pattern", () => {
      expect(toolSummary("grep", { pattern: "TODO" })).toBe("TODO");
    });
    it("clarify 取 args.title，无 title 回退 'clarify'", () => {
      expect(toolSummary("clarify", { title: "需求澄清" })).toBe("需求澄清");
      expect(toolSummary("clarify", { title: "" })).toBe("clarify");
      expect(toolSummary("clarify", {})).toBe("clarify");
    });
    it("未知工具回退 toolName", () => {
      expect(toolSummary("custom_tool", { x: 1 })).toBe("custom_tool");
    });
    it("args 为 null 回退 toolName", () => {
      expect(toolSummary("write", null)).toBe("write");
    });
    it("path 非字符串回退 toolName", () => {
      expect(toolSummary("write", { path: 123 })).toBe("write");
    });
  });

  describe("isClarifyTool / isFileMutationTool", () => {
    it("isClarifyTool 仅 clarify 为 true", () => {
      expect(isClarifyTool("clarify")).toBe(true);
      expect(isClarifyTool("write")).toBe(false);
    });
    it("isFileMutationTool 仅 write/edit 为 true", () => {
      expect(isFileMutationTool("write")).toBe(true);
      expect(isFileMutationTool("edit")).toBe(true);
      expect(isFileMutationTool("read")).toBe(false);
      expect(isFileMutationTool("bash")).toBe(false);
    });
  });

  describe("diffLineKind", () => {
    it("+++ / --- / @@ 归 hunk", () => {
      expect(diffLineKind("+++ b/file")).toBe("hunk");
      expect(diffLineKind("--- a/file")).toBe("hunk");
      expect(diffLineKind("@@ -1,2 +1,2 @@")).toBe("hunk");
    });
    it("+ 行归 add", () => {
      expect(diffLineKind("+new line")).toBe("add");
    });
    it("- 行归 del", () => {
      expect(diffLineKind("-old line")).toBe("del");
    });
    it("其余归 context", () => {
      expect(diffLineKind(" context")).toBe("context");
      expect(diffLineKind("plain")).toBe("context");
    });
  });

  describe("editsToDiffLines", () => {
    it("每个 edit 产 -old / +new 行", () => {
      const edits = [{ oldText: "a\nb", newText: "a\nB" }];
      expect(editsToDiffLines(edits)).toEqual(["-a", "-b", "+a", "+B"]);
    });
    it("多行 old/new 拆分", () => {
      expect(editsToDiffLines([{ oldText: "x", newText: "y\nz" }])).toEqual(["-x", "+y", "+z"]);
    });
    it("非数组返回空", () => {
      expect(editsToDiffLines(null)).toEqual([]);
      expect(editsToDiffLines(undefined)).toEqual([]);
      expect(editsToDiffLines("nope")).toEqual([]);
    });
    it("跳过非对象元素", () => {
      expect(editsToDiffLines([null, "x", { oldText: "a", newText: "b" }])).toEqual(["-a", "+b"]);
    });
  });
});

describe("attachment", () => {
  it("剥离尾部 [Attached files: ...] 提示", () => {
    expect(stripAttachmentHint("看下这个\n\n[Attached files: data.csv]")).toBe("看下这个");
  });
  it("多个文件路径", () => {
    expect(stripAttachmentHint("hi\n\n[Attached files: a.csv, b.csv]")).toBe("hi");
  });
  it("无提示时原样返回（trim）", () => {
    expect(stripAttachmentHint("普通消息")).toBe("普通消息");
  });
  it("非字符串返回空", () => {
    expect(stripAttachmentHint(undefined)).toBe("");
    expect(stripAttachmentHint(null)).toBe("");
  });
  it("不剥离非尾部提示", () => {
    // 只匹配尾部，中间的 hint 不剥
    expect(stripAttachmentHint("[Attached files: x.csv]\n\n正文")).toBe("[Attached files: x.csv]\n\n正文");
  });
});

describe("workspacePath", () => {
  describe("isLocalImgSrc", () => {
    it("http/data/blob/file 远程 URL 返回 false", () => {
      expect(isLocalImgSrc("https://a.com/x.png")).toBe(false);
      expect(isLocalImgSrc("data:image/png;base64,xx")).toBe(false);
      expect(isLocalImgSrc("blob:xxx")).toBe(false);
      expect(isLocalImgSrc("file:///tmp/x.png")).toBe(false);
    });
    it("本地路径返回 true", () => {
      expect(isLocalImgSrc("output/x.png")).toBe(true);
      expect(isLocalImgSrc("/opt/data/profiles/p/home/x.png")).toBe(true);
    });
    it("null 返回 false", () => {
      expect(isLocalImgSrc(null)).toBe(false);
    });
  });

  describe("normalizeWorkspacePath", () => {
    it("剥 file:// 前缀", () => {
      expect(normalizeWorkspacePath("file:///opt/data/profiles/p/home/x.png")).toBe("home/x.png");
    });
    it("剥 ./ 前缀", () => {
      expect(normalizeWorkspacePath("./home/x.png")).toBe("home/x.png");
    });
    it("剥 /opt/data/profiles/<profile>/ 前缀", () => {
      expect(normalizeWorkspacePath("/opt/data/profiles/myprofile/home/x.png")).toBe("home/x.png");
    });
    it("兼容旧 /profiles/<profile>/ 格式", () => {
      expect(normalizeWorkspacePath("/profiles/myprofile/home/x.png")).toBe("home/x.png");
    });
    it("裸绝对路径去前导斜杠", () => {
      expect(normalizeWorkspacePath("/home/x.png")).toBe("home/x.png");
    });
    it("相对路径原样", () => {
      expect(normalizeWorkspacePath("output/x.png")).toBe("output/x.png");
    });
  });
});
