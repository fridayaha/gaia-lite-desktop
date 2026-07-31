/**
 * clarify 工具 —— 让 dev agent（skill-creator）在需要需求澄清或二次确认时，
 * 调用一个结构化问卷工具，而不是写一长串 markdown 提问。
 *
 * 工作原理（pi SDK 的 human-in-the-loop 扩展点）：
 * - 通过 `createAgentSession({ customTools })` 注册。
 * - agent 调用 clarify 时，pi 发出 `tool_execution_start`（args=问卷 schema），
 *   然后 `await` 我们的 `execute()`。
 * - `execute()` 返回一个 pending Promise，存入 `pendingClarify` Map 阻塞 agent turn。
 * - 前端把运行中的工具卡渲染成可填写表单；用户提交后经
 *   `POST .../tools/:toolCallId/response` → `tool_response` 命令 → `resolveClarify()`
 *   解析 pending Promise，答案作为 `tool_execution_end.result` 返回给模型。
 * - `AbortSignal` 触发时（abort/会话销毁）reject，turn 结束。
 *
 * 仅 dev role 装配（见 skill-bootstrap.ts）。
 */
import { Type } from "@earendil-works/pi-ai";
import { defineTool } from "@earendil-works/pi-coding-agent";

// ── 共享类型（前端从 tool.args / tool.result.details 反序列化时对齐） ──

export type ClarifyQuestionType = "text" | "single" | "multi" | "confirm";

export interface ClarifyQuestion {
  /** 简短标识，如 scene / engine。前端按 id 回填答案。 */
  id: string;
  /** 问题原文，展示给用户。 */
  text: string;
  type: ClarifyQuestionType;
  /** single/multi 的候选选项。 */
  options?: string[];
  /** text 类型的输入提示。 */
  placeholder?: string;
  required?: boolean;
  /** single/multi 是否允许「其他」自定义输入。 */
  allowOther?: boolean;
}

export interface ClarifyArgs {
  title: string;
  intro?: string;
  questions: ClarifyQuestion[];
}

/** 答案：text→string，single→string，multi→string[]，confirm→boolean。 */
export type ClarifyAnswers = Record<string, string | string[] | boolean>;

export interface ClarifyDetails {
  answers: ClarifyAnswers;
  title: string;
  intro?: string;
}

interface ClarifyResult {
  content: { type: "text"; text: string }[];
  details: ClarifyDetails;
}

// ── pending 通道 ──

export interface PendingClarify {
  resolve: (r: ClarifyResult) => void;
  reject: (e: Error) => void;
  /** 问卷参数，resolve 时用于格式化答案文本。 */
  args: ClarifyArgs;
}

export type PendingClarifyMap = Map<string, PendingClarify>;

// ── schema ──

const questionSchema = Type.Object({
  id: Type.String({ description: "简短标识，如 scene / engine" }),
  text: Type.String({ description: "问题原文，展示给用户" }),
  type: Type.Union(
    [
      Type.Literal("text"),
      Type.Literal("single"),
      Type.Literal("multi"),
      Type.Literal("confirm"),
    ],
    {
      description:
        "text=自由文本；single=单选；multi=多选；confirm=是/否确认",
    },
  ),
  options: Type.Optional(
    Type.Array(Type.String(), { description: "single/multi 的候选选项" }),
  ),
  placeholder: Type.Optional(
    Type.String({ description: "text 类型的输入提示" }),
  ),
  required: Type.Optional(
    Type.Boolean({
      description:
        "默认 false（可选，用户可跳过）。只有「缺这个答案就完全无法推进」时才设 true；" +
        "其余问题留 false，让用户可跳过，你用合理默认值填补。",
    }),
  ),
  allowOther: Type.Optional(
    Type.Boolean({ description: "single/multi 是否允许「其他」自定义输入" }),
  ),
});

// ── 工厂 ──

export function createClarifyTool(pending: PendingClarifyMap) {
  return defineTool({
    name: "clarify",
    label: "Clarify",
    description:
      "向用户提出结构化澄清问题或二次确认。当需要澄清需求、收集偏好或确认设计决策时调用——" +
      "不要用纯文本罗列问题。用户通过 UI 表单填写后提交，答案作为结构化结果返回，你再据此推进。" +
      "尽量只问 1-3 个最关键的问题，选项要具体可选；能从上下文推断的不要问，需求已清楚就别调用。",
    promptSnippet:
      "clarify(title, intro?, questions[]): ask the user structured clarifying questions via a UI form; blocks until they submit answers.",
    promptGuidelines: [
      "需要澄清或确认时调用 clarify，传入标题、引导语和结构化问题列表，不要写长 markdown 提问。",
      "先从用户已说的话和上下文推断，只问真正推断不了的缺口；需求清楚就跳过 clarify 直接开始。",
      "一次尽量 1-3 个问题，宁少勿多；优先用 confirm（一键是/否）代替 text/single，降低用户负担。",
      "问题默认不必填（required 不填或 false）——只在「没有这个答案就无法推进」时才设 required: true；" +
        "用户跳过的题你用合理默认值填补并继续，不要反复追问。",
      "问卷的标题、引导语、问题与选项一律使用简体中文（中国大陆），不要使用繁体中文。",
    ],
    parameters: Type.Object({
      title: Type.String({ description: "问卷标题，如「需求调研」" }),
      intro: Type.Optional(
        Type.String({ description: "引导语，解释为什么问这些问题" }),
      ),
      questions: Type.Array(questionSchema, {
        description: "问题列表，3-6 个为宜",
      }),
    }),
    executionMode: "sequential",
    async execute(toolCallId, params, signal) {
      const args = params as ClarifyArgs;
      return new Promise<ClarifyResult>((resolve, reject) => {
        if (signal?.aborted) {
          reject(new Error("clarify aborted"));
          return;
        }
        const onAbort = () => {
          if (pending.delete(toolCallId)) reject(new Error("clarify aborted"));
        };
        signal?.addEventListener("abort", onAbort, { once: true });
        pending.set(toolCallId, {
          args,
          resolve: (r) => {
            signal?.removeEventListener("abort", onAbort);
            resolve(r);
          },
          reject: (e) => {
            signal?.removeEventListener("abort", onAbort);
            reject(e);
          },
        });
      });
    },
  });
}

// ── 解析（由 worker 的 tool_response 命令调用） ──

/**
 * 用用户提交的答案解析阻塞中的 clarify 工具。
 * @returns true=找到并已 resolve；false=无对应 pending（已超时/被 abort/未知 id）
 */
export function resolveClarify(
  pending: PendingClarifyMap,
  toolCallId: string,
  answers: ClarifyAnswers,
): boolean {
  const entry = pending.get(toolCallId);
  if (!entry) return false;
  pending.delete(toolCallId);
  entry.resolve({
    content: [{ type: "text", text: formatAnswers(entry.args, answers) }],
    details: { answers, title: entry.args.title, intro: entry.args.intro },
  });
  return true;
}

/** 把结构化答案格式化成给模型读的文本。 */
export function formatAnswers(args: ClarifyArgs, answers: ClarifyAnswers): string {
  const head = args.title ? `用户对「${args.title}」的回答：` : "用户的回答：";
  const lines = args.questions.map((q, i) => {
    const v = answers[q.id];
    let valStr: string;
    if (v === undefined || v === null || v === "") {
      valStr = "（未回答）";
    } else if (Array.isArray(v)) {
      valStr = v.length ? v.join("、") : "（未回答）";
    } else if (typeof v === "boolean") {
      valStr = v ? "是" : "否";
    } else {
      valStr = String(v);
    }
    return `${i + 1}. ${q.text}：${valStr}`;
  });
  return [head, ...lines].join("\n");
}
