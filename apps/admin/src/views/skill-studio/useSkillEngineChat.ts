/**
 * useSkillEngineChat — admin Skill Studio 的聊天状态适配器。
 *
 * 复用 detail.vue 持有的 useEngineSession 实例（单例：dev/debug 两个会话的 SSE/鉴权/
 * backfill 由 detail.vue 统一管理），响应式转换成 @ua/chat 的 ChatMessages 所消费的
 * 平行模型：messages[].content + _toolCalls[] + streamingContent + live toolCalls。
 *
 * 工具卡走共享 ToolCard 的「丰富形态」：透传 toolName/args/rawResult，由 ToolCard 按
 * bash/write/edit/grep/read/ls/clarify 分形态渲染（含 diff 着色 + clarify 表单）。
 *
 * 代价：丢失 parts[] 的"文本→工具→文本"交错，改 hermes-webui 风格（统一 UX 预期）。
 */
import { computed, type Ref } from "vue";
import type { useEngineSession, ToolCall as EngineToolCall } from "./useEngineSession";
import type { EngineRole } from "@/api/manager/skill-engine";
import { extractResultText, toolSummary } from "@ua/chat";

/** 共享 ToolCard 接受的工具调用形状（丰富形态）。 */
export interface SkillToolCall {
  id: string;
  name: string;
  toolName: string;
  status: "running" | "success" | "error";
  args?: Record<string, unknown> | null;
  rawResult?: unknown;
  /** 简单形态兜底文本（未知工具 / 无结构化 result 时）。 */
  result?: string;
  preview?: string;
  statusLabel?: string;
}

/** ChatMessages 期望的平行模型消息。 */
export interface SkillChatMessage {
  id: string;
  role: "user" | "assistant" | "system";
  content: string;
  isError?: boolean;
  _toolCalls?: SkillToolCall[];
  _ts?: number;
  streaming?: boolean;
}

const ENGINE_STATUS_TO_CHAT: Record<EngineToolCall["status"], SkillToolCall["status"]> = {
  running: "running",
  done: "success",
  error: "error",
};

function mapToolCall(t: EngineToolCall): SkillToolCall {
  const status = ENGINE_STATUS_TO_CHAT[t.status] ?? "running";
  return {
    id: t.id,
    name: t.toolName,
    toolName: t.toolName,
    status,
    args: t.args,
    rawResult: t.result,
    result: extractResultText(t.result),
    preview: toolSummary(t.toolName, t.args),
    statusLabel: status,
  };
}

export function useSkillEngineChat(
  engine: ReturnType<typeof useEngineSession>,
  role: EngineRole,
  workspaceId: Ref<string>,
) {
  const session = engine.sessions[role];

  /**
   * 把 skill-engine 的 parts[] 多消息模型折叠成「回合」：一条 user 消息后的连续
   * assistant 消息（引擎每个 tool 子循环会 message_end 落定当前消息、turn_start 再建
   * 下一条）合并为一个回合——拼接文本、归并工具、合并 streaming 标志。
   * 旧 admin ChatPanel 靠 turn 折叠吸收这些子消息；共享 ChatMessages 逐条渲染会出
   * 现多个空气泡，故在此折叠后再喂给 ChatMessages。
   */
  interface FoldedTurn {
    id: string;
    role: "user" | "assistant" | "system";
    content: string;
    toolCalls: EngineToolCall[];
    ts?: number;
    streaming: boolean;
  }
  const turns = computed<FoldedTurn[]>(() => {
    const out: FoldedTurn[] = [];
    for (const m of session.messages) {
      const ts = Date.parse(m.createdAt) / 1000;
      const tsVal = Number.isFinite(ts) ? ts : undefined;
      if (m.role !== "assistant") {
        out.push({ id: m.id, role: m.role, content: m.content, toolCalls: [], ts: tsVal, streaming: false });
        continue;
      }
      let cur = out[out.length - 1];
      if (!cur || cur.role !== "assistant") {
        cur = { id: m.id, role: "assistant", content: "", toolCalls: [], ts: tsVal, streaming: false };
        out.push(cur);
      }
      if (m.content) cur.content = cur.content ? `${cur.content}\n${m.content}` : m.content;
      cur.toolCalls = [...cur.toolCalls, ...(m.toolCalls || [])];
      if (m.streaming) {
        cur.streaming = true;
        if (tsVal) cur.ts = tsVal;
      }
    }
    return out;
  });

  /** 平行模型消息列表：仅 fully-settled 回合（streaming 回合由 ChatMessages 实时回合渲染）。
   *  过滤无内容无工具的空回合（落定的 leftover）。 */
  const messages = computed<SkillChatMessage[]>(() =>
    turns.value
      .filter((t) => !t.streaming && !(t.role === "assistant" && !t.content && t.toolCalls.length === 0))
      .map((t) => ({
        id: t.id,
        role: t.role,
        content: t.content,
        _toolCalls: t.toolCalls.map(mapToolCall),
        _ts: t.ts,
        streaming: false,
      })),
  );

  /** 当前正在流式的回合（含本回合已落定的工具子消息 + streaming 文本消息）。 */
  const streamingTurn = computed(() => turns.value.find((t) => t.role === "assistant" && t.streaming) ?? null);

  /** 流式累积全文（ChatMessages 的 streamingContent prop）。 */
  const streamingContent = computed(() => streamingTurn.value?.content ?? "");

  /** 当前回合全部 live 工具调用（含已落定子消息里的工具，归并展示）。 */
  const toolCalls = computed<SkillToolCall[]>(() =>
    (streamingTurn.value?.toolCalls || []).map(mapToolCall),
  );

  const isStreaming = computed(() => session.isStreaming);
  const isEmpty = computed(() => messages.value.length === 0 && !isStreaming.value);

  return {
    // 状态（ChatMessages props）
    messages,
    isStreaming,
    isEmpty,
    streamingContent,
    toolCalls,
    // skill-engine 无 thinking/activity 事件（hermes 专有），留空
    thinkingText: computed(() => ""),
    thinkingStatus: computed(() => ""),
    activityEvents: computed<unknown[]>(() => []),
    // 引擎会话状态（detail.vue 顶栏/连接态用）
    status: computed(() => session.status),
    error: computed(() => session.error),
    connected: computed(() => session.connected),
    model: computed(() => session.model),
    // 操作
    connect: () => engine.connect(role),
    disconnect: () => engine.disconnect(role),
    reconnect: () => engine.reconnect(role),
    send: (text: string) => engine.send(role, text),
    steer: (text: string) => engine.steer(role, text),
    abort: () => engine.abort(role),
    refreshState: () => engine.refreshState(role),
    // workspaceId 透传（clarify 提交需要）
    workspaceId,
  };
}

export type SkillEngineChat = ReturnType<typeof useSkillEngineChat>;
