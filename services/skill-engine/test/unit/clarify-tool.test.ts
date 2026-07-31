import { describe, it, expect } from "vitest";
import {
  createClarifyTool,
  resolveClarify,
  formatAnswers,
  type PendingClarifyMap,
  type ClarifyArgs,
  type ClarifyAnswers,
} from "../../src/engine/clarify-tool.js";

const sampleArgs: ClarifyArgs = {
  title: "需求调研",
  intro: "了解你想做的联网搜索技能",
  questions: [
    { id: "scene", text: "使用场景", type: "text" },
    { id: "engine", text: "搜索引擎", type: "single", options: ["DuckDuckGo", "百度"] },
    { id: "fmt", text: "输出格式", type: "multi", options: ["列表", "摘要", "报告"] },
    { id: "cn", text: "需要中文搜索吗", type: "confirm" },
  ],
};

describe("clarify-tool", () => {
  describe("createClarifyTool", () => {
    it("registers a tool named clarify with the expected shape", () => {
      const tool = createClarifyTool(new Map());
      expect(tool.name).toBe("clarify");
      expect(tool.executionMode).toBe("sequential");
      expect(tool.parameters).toBeTruthy();
    });
  });

  describe("execute + resolveClarify", () => {
    it("execute blocks until resolveClarify supplies answers", async () => {
      const pending: PendingClarifyMap = new Map();
      const tool = createClarifyTool(pending);

      const execPromise = tool.execute(
        "tc-1",
        sampleArgs as any,
        undefined,
        undefined,
        undefined as any,
      );

      // Pending while no answers submitted.
      expect(pending.has("tc-1")).toBe(true);
      // Hasn't resolved yet (give it a microtask tick to be sure).
      let resolved = false;
      void execPromise.then(() => {
        resolved = true;
      });
      await Promise.resolve();
      expect(resolved).toBe(false);

      const answers: ClarifyAnswers = {
        scene: "查最新AI新闻",
        engine: "DuckDuckGo",
        fmt: ["列表", "摘要"],
        cn: true,
      };
      const ok = resolveClarify(pending, "tc-1", answers);
      expect(ok).toBe(true);
      expect(pending.has("tc-1")).toBe(false); // cleared after resolve

      const result = await execPromise;
      // content text carries formatted answers (model-facing).
      expect(result.content).toHaveLength(1);
      expect(result.content[0].type).toBe("text");
      const text = result.content[0].text;
      expect(text).toContain("需求调研");
      expect(text).toContain("1. 使用场景：查最新AI新闻");
      expect(text).toContain("DuckDuckGo");
      expect(text).toContain("列表、摘要");
      expect(text).toContain("是");
      // details carries structured answers (UI-facing).
      expect(result.details.answers).toEqual(answers);
      expect(result.details.title).toBe("需求调研");
      expect(result.details.intro).toBe("了解你想做的联网搜索技能");
    });

    it("resolveClarify returns false for an unknown toolCallId", () => {
      const pending: PendingClarifyMap = new Map();
      createClarifyTool(pending);
      expect(resolveClarify(pending, "unknown", {})).toBe(false);
    });

    it("abort signal rejects the pending execute and clears the entry", async () => {
      const pending: PendingClarifyMap = new Map();
      const tool = createClarifyTool(pending);
      const controller = new AbortController();

      const execPromise = tool.execute(
        "tc-2",
        sampleArgs as any,
        controller.signal,
        undefined,
        undefined as any,
      );

      expect(pending.has("tc-2")).toBe(true);
      controller.abort();
      await expect(execPromise).rejects.toThrow("clarify aborted");
      expect(pending.has("tc-2")).toBe(false);
    });

    it("execute rejects immediately if the signal is already aborted", async () => {
      const pending: PendingClarifyMap = new Map();
      const tool = createClarifyTool(pending);
      const controller = new AbortController();
      controller.abort();

      await expect(
        tool.execute(
          "tc-3",
          sampleArgs as any,
          controller.signal,
          undefined,
          undefined as any,
        ),
      ).rejects.toThrow("clarify aborted");
      expect(pending.has("tc-3")).toBe(false);
    });
  });

  describe("formatAnswers", () => {
    it("formats each question type and marks unanswered", () => {
      const args: ClarifyArgs = {
        title: "调研",
        questions: [
          { id: "a", text: "文本题", type: "text" },
          { id: "b", text: "单选题", type: "single", options: ["x", "y"] },
          { id: "c", text: "多选题", type: "multi", options: ["p", "q"] },
          { id: "d", text: "确认题", type: "confirm" },
          { id: "e", text: "未答题", type: "text" },
        ],
      };
      const answers: ClarifyAnswers = {
        a: "hello",
        b: "x",
        c: ["p", "q"],
        d: false,
      };
      const out = formatAnswers(args, answers);
      expect(out).toBe(
        [
          "用户对「调研」的回答：",
          "1. 文本题：hello",
          "2. 单选题：x",
          "3. 多选题：p、q",
          "4. 确认题：否",
          "5. 未答题：（未回答）",
        ].join("\n"),
      );
    });

    it("falls back when title is absent", () => {
      const out = formatAnswers(
        { questions: [{ id: "a", text: "Q", type: "text" }] },
        { a: "ans" },
      );
      expect(out).toContain("用户的回答：");
      expect(out).toContain("1. Q：ans");
    });
  });
});
