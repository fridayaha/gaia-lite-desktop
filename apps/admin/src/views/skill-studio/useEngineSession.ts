/**
 * useEngineSession — 管理一个工作区下 dev/debug 两个引擎会话的 SSE 连接与状态。
 *
 * 后端 SSE 协议（经 Manager 代理透传，不缓冲）：
 *   data: {json}\n\n
 *   msg 类型：connected / status / event{eventType,data} / response / heartbeat
 *   eventType 已知值：turn_start / message_update / message_end / turn_end /
 *                     tool_execution_start / tool_execution_update / tool_execution_end。
 *
 * 工具事件按 toolCallId 聚合到当前 assistant 消息的有序 parts 中（文本与工具调用
 * 按时序交错，parts 是唯一真相；toolCalls 为 parts 派生的扁平列表，兼容历史字段）。
 *
 * sid = role（"dev" | "debug"），非 UUID。
 */
import { ref, reactive, type Ref } from "vue";
import { getToken, formatToken } from "@/utils/auth";
import { useUserStoreHook } from "@/store/modules/user";
import {
  startSessionApi,
  stopSessionApi,
  promptApi,
  steerApi,
  followUpApi,
  abortApi,
  getStateApi,
  listMessagesApi,
  type EngineRole,
  type InstanceState,
  type EngineMessage,
} from "@/api/manager/skill-engine";

export type SessionStatus =
  | "idle"
  | "connecting"
  | "ready"
  | "error"
  | "shutting_down";

export type ToolCallStatus = "running" | "done" | "error";

export interface ToolCall {
  id: string; // toolCallId
  toolName: string;
  args: Record<string, unknown> | null;
  status: ToolCallStatus;
  result?: unknown; // tool_execution_end 的 AgentToolResult
  partial?: unknown; // 最近一次 tool_execution_update 的 partialResult
  startedAt: string;
}

export type AssistantPart =
  | { kind: "text"; text: string }
  | { kind: "tool"; tool: ToolCall };

export interface ChatMessage {
  id: string;
  role: "user" | "assistant" | "system";
  content: string; // user 文本；assistant 为累积文本（兼容/回填用）
  parts: AssistantPart[]; // assistant 有序片段；user 为空
  toolCalls: ToolCall[]; // parts 中 tool 片段的扁平列表（兼容历史字段）
  createdAt: string;
  streaming?: boolean;
}

interface RoleSession {
  status: SessionStatus;
  isStreaming: boolean;
  model: { provider: string; modelId: string } | null;
  messageCount: number;
  error: string | null;
  messages: ChatMessage[];
  connected: boolean;
}

type EngineWorkerMessage = {
  type: "connected" | "status" | "event" | "response" | "heartbeat" | string;
  sessionId?: string;
  status?: string;
  error?: string;
  eventType?: string;
  data?: Record<string, unknown>;
  command?: string;
  success?: boolean;
  ts?: number;
};

const newRoleSession = (): RoleSession => ({
  status: "idle",
  isStreaming: false,
  model: null,
  messageCount: 0,
  error: null,
  messages: [],
  connected: false,
});

let msgSeq = 0;
const localId = () => `local-${Date.now()}-${msgSeq++}`;

export function useEngineSession(workspaceId: Ref<string>) {
  const sessions = reactive<Record<EngineRole, RoleSession>>({
    dev: newRoleSession(),
    debug: newRoleSession(),
  });

  // Per-role SSE controllers + reconnect state (non-reactive).
  const controllers: Record<EngineRole, AbortController | null> = {
    dev: null,
    debug: null,
  };
  const intentionalClose: Record<EngineRole, boolean> = { dev: false, debug: false };
  const reconnectDelay: Record<EngineRole, number> = { dev: 1, debug: 1 };

  // ── Authenticated fetch with 401 refresh + single retry ──────────
  async function authedFetch(
    url: string,
    role: EngineRole,
    retried = false,
  ): Promise<Response> {
    const token = getToken();
    const res = await fetch(url, {
      headers: {
        Accept: "text/event-stream",
        Authorization: formatToken(token.accessToken),
      },
      signal: controllers[role]?.signal,
    });
    if (res.status !== 401) return res;
    if (retried) {
      useUserStoreHook().logOut();
      throw new Error("Unauthorized");
    }
    const refreshRes: any = await useUserStoreHook().handRefreshToken({
      refreshToken: token.refreshToken,
    });
    const newToken = refreshRes?.data?.accessToken ?? refreshRes?.accessToken;
    if (!newToken) {
      useUserStoreHook().logOut();
      throw new Error("Unauthorized");
    }
    return fetch(url, {
      headers: {
        Accept: "text/event-stream",
        Authorization: formatToken(newToken),
      },
      signal: controllers[role]?.signal,
    });
  }

  // ── SSE reader loop ──────────────────────────────────────────────
  async function openStream(role: EngineRole) {
    const wid = workspaceId.value;
    const url = `/api/skill-engine/workspaces/${wid}/sessions/${role}/events`;
    const s = sessions[role];

    let res: Response;
    try {
      res = await authedFetch(url, role);
    } catch (err) {
      if (!intentionalClose[role]) scheduleReconnect(role);
      return;
    }
    if (!res.ok || !res.body) {
      s.status = "error";
      s.error = `SSE 连接失败 (${res.status})`;
      if (!intentionalClose[role]) scheduleReconnect(role);
      return;
    }

    const reader = res.body.getReader();
    const decoder = new TextDecoder();
    let buf = "";

    try {
      // eslint-disable-next-line no-constant-condition
      while (true) {
        const { done, value } = await reader.read();
        if (done) break;
        buf += decoder.decode(value, { stream: true });
        const lines = buf.split("\n");
        buf = lines.pop() || ""; // keep incomplete trailing line
        for (const raw of lines) {
          const line = raw.trim();
          if (!line.startsWith("data:")) continue;
          const payload = line.slice(5).trim();
          if (!payload) continue;
          try {
            handleMessage(role, JSON.parse(payload) as EngineWorkerMessage);
          } catch {
            // ignore malformed JSON
          }
        }
      }
    } catch {
      // network error / abort
    } finally {
      try {
        reader.releaseLock();
      } catch {
        /* noop */
      }
    }

    s.connected = false;
    if (!intentionalClose[role]) scheduleReconnect(role);
  }

  // ── Event dispatch ───────────────────────────────────────────────
  function handleMessage(role: EngineRole, msg: EngineWorkerMessage) {
    const s = sessions[role];
    switch (msg.type) {
      case "connected":
        s.connected = true;
        s.status = "ready";
        s.error = null;
        reconnectDelay[role] = 1; // reset backoff on clean connect
        break;
      case "status":
        if (msg.status === "ready") {
          s.status = "ready";
        } else if (msg.status === "error") {
          s.status = "error";
          s.error = msg.error ?? "引擎初始化失败";
        } else if (msg.status === "shutting_down") {
          s.status = "shutting_down";
        } else if (msg.status === "initializing") {
          s.status = "connecting";
        }
        break;
      case "heartbeat":
        // keep-alive, no UI action
        break;
      case "response":
        // command ack; 2A: no-op (messages arrive via events)
        break;
      case "event":
        handleEvent(role, msg.eventType ?? "", msg.data ?? {});
        break;
      default:
        break;
    }
  }

  function handleEvent(role: EngineRole, eventType: string, data: Record<string, unknown>) {
    const s = sessions[role];
    const lastAssistant = () => {
      for (let i = s.messages.length - 1; i >= 0; i--) {
        if (s.messages[i].role === "assistant") return s.messages[i];
      }
      return null;
    };
    const ensureStreamingAssistant = (): ChatMessage => {
      const last = lastAssistant();
      if (last && last.streaming) return last;
      const m: ChatMessage = {
        id: localId(),
        role: "assistant",
        content: "",
        parts: [],
        toolCalls: [],
        createdAt: new Date().toISOString(),
        streaming: true,
      };
      s.messages.push(m);
      return m;
    };

    // 在当前 streaming assistant 消息的 parts 末尾追加/更新文本片段。
    // 末尾已是 text 片段则就地替换（累积），否则新建 text 片段。
    const pushTextPart = (m: ChatMessage, text: string) => {
      const last = m.parts[m.parts.length - 1];
      if (last && last.kind === "text") {
        last.text = text;
      } else {
        m.parts.push({ kind: "text", text });
      }
      m.content = text;
    };

    // 按 toolCallId 在 parts 中找 tool 片段（同一工具的 start/update/end 聚合）。
    const findToolPart = (m: ChatMessage, toolCallId: string) =>
      m.parts.find(
        (p): p is Extract<AssistantPart, { kind: "tool" }> =>
          p.kind === "tool" && p.tool.id === toolCallId,
      );

    switch (eventType) {
      case "turn_start":
        s.isStreaming = true;
        ensureStreamingAssistant();
        break;
      case "message_update": {
        // pi SDK message_update carries the accumulated assistant partial in
        // data.message.content[] (array of content blocks). Reconstruct the
        // full text so far from the text blocks and replace — robust to
        // text_start/text_delta sub-events regardless of their exact shape.
        const msg = data.message as
          | { content?: Array<{ type?: string; text?: string }> }
          | undefined;
        const blocks = msg?.content;
        if (Array.isArray(blocks)) {
          const text = blocks
            .filter((b) => b && b.type === "text" && typeof b.text === "string")
            .map((b) => b.text as string)
            .join("");
          if (text.length > 0) {
            const m = ensureStreamingAssistant();
            pushTextPart(m, text);
          }
        }
        break;
      }
      case "message_end": {
        const last = lastAssistant();
        if (last) last.streaming = false;
        break;
      }
      case "tool_execution_start": {
        const toolCallId = data.toolCallId as string | undefined;
        const toolName = data.toolName as string | undefined;
        if (!toolCallId || !toolName) break;
        const m = ensureStreamingAssistant();
        // 已存在则不重复加（幂等，防重放）
        if (findToolPart(m, toolCallId)) break;
        const tool: ToolCall = {
          id: toolCallId,
          toolName,
          args: (data.args as Record<string, unknown>) ?? null,
          status: "running",
          startedAt: new Date().toISOString(),
        };
        m.parts.push({ kind: "tool", tool });
        m.toolCalls.push(tool);
        break;
      }
      case "tool_execution_update": {
        const toolCallId = data.toolCallId as string | undefined;
        if (!toolCallId) break;
        const m = lastAssistant();
        if (!m) break;
        const part = findToolPart(m, toolCallId);
        if (part) part.tool.partial = data.partialResult;
        break;
      }
      case "tool_execution_end": {
        const toolCallId = data.toolCallId as string | undefined;
        if (!toolCallId) break;
        const m = lastAssistant();
        if (!m) break;
        const part = findToolPart(m, toolCallId);
        if (part) {
          part.tool.result = data.result;
          part.tool.status = data.isError ? "error" : "done";
        }
        break;
      }
      case "turn_end":
        s.isStreaming = false;
        s.messageCount += 1;
        {
          const last = lastAssistant();
          if (last) last.streaming = false;
        }
        break;
      default: {
        // 未知 eventType — 永不崩溃。仅调试日志，不再把原始事件塞进 toolCalls
        //（工具事件已有显式 case；这里的未知事件无渲染语义）。
        // eslint-disable-next-line no-console
        console.debug("[skill-studio] unhandled event:", eventType, data);
        break;
      }
    }
  }

  // ── Reconnect with exponential backoff ───────────────────────────
  function scheduleReconnect(role: EngineRole) {
    if (intentionalClose[role]) return;
    const delay = reconnectDelay[role];
    reconnectDelay[role] = Math.min(delay * 2, 30);
    setTimeout(() => {
      if (intentionalClose[role]) return;
      void reconnect(role);
    }, delay * 1000);
  }

  // ── Public API ───────────────────────────────────────────────────

  /** Start session + open SSE + backfill history. */
  async function connect(role: EngineRole) {
    const s = sessions[role];
    if (s.connected || s.status === "connecting") return;
    intentionalClose[role] = false;
    s.status = "connecting";
    s.error = null;
    try {
      await startSessionApi(workspaceId.value, role);
    } catch {
      // start may fail if already running — continue to open stream
    }
    controllers[role] = new AbortController();
    // Backfill history (non-blocking; live stream takes priority)
    void backfillHistory(role);
    void openStream(role);
    // Initialize model/state from state endpoint
    void refreshState(role);
  }

  async function backfillHistory(role: EngineRole) {
    try {
      const res = await listMessagesApi(workspaceId.value, role);
      const msgs: ChatMessage[] = (res.messages as EngineMessage[])
        .slice()
        .sort((a, b) => a.seq - b.seq)
        .map((m) => {
          const role = (m.sender as "user" | "assistant" | "system") || "assistant";
          // 后端 turn_end 持久化时把有序 parts 存进 tool_calls 列；有则直接还原
          // （保留文本/工具交错），无则退化为单文本片段。
          const persistedParts = Array.isArray(m.toolCalls)
            ? (m.toolCalls as unknown as AssistantPart[])
            : null;
          const parts: AssistantPart[] =
            role === "assistant"
              ? persistedParts && persistedParts.length
                ? persistedParts
                : [{ kind: "text", text: m.content }]
              : [];
          return {
            id: m.id,
            role,
            content: m.content,
            parts,
            toolCalls: parts
              .filter((p): p is Extract<AssistantPart, { kind: "tool" }> => p.kind === "tool")
              .map((p) => p.tool),
            createdAt: m.createdAt,
          };
        });
      // Only replace if we have no live streaming message in progress
      const s = sessions[role];
      const streaming = s.messages.find((m) => m.streaming);
      s.messages = msgs;
      if (streaming) s.messages.push(streaming);
    } catch {
      // history optional
    }
  }

  async function refreshState(role: EngineRole) {
    try {
      const st: InstanceState = await getStateApi(workspaceId.value, role);
      const s = sessions[role];
      s.isStreaming = st.isStreaming;
      s.model = st.model;
      s.messageCount = st.messageCount;
    } catch {
      // state optional
    }
  }

  async function reconnect(role: EngineRole) {
    controllers[role]?.abort();
    controllers[role] = new AbortController();
    await refreshState(role);
    void backfillHistory(role);
    void openStream(role);
  }

  async function disconnect(role: EngineRole) {
    intentionalClose[role] = true;
    controllers[role]?.abort();
    controllers[role] = null;
    sessions[role].connected = false;
    try {
      await stopSessionApi(workspaceId.value, role);
    } catch {
      // best-effort
    }
    sessions[role].status = "idle";
  }

  async function send(role: EngineRole, message: string) {
    const s = sessions[role];
    if (!message.trim()) return;
    // Optimistically push the user message
    s.messages.push({
      id: localId(),
      role: "user",
      content: message,
      parts: [],
      toolCalls: [],
      createdAt: new Date().toISOString(),
    });
    try {
      await promptApi(workspaceId.value, role, message);
    } catch {
      s.error = "发送失败，请重试";
    }
  }

  async function steer(role: EngineRole, message: string) {
    await steerApi(workspaceId.value, role, message);
  }

  async function followUp(role: EngineRole, message: string) {
    await followUpApi(workspaceId.value, role, message);
  }

  async function abort(role: EngineRole) {
    try {
      await abortApi(workspaceId.value, role);
    } catch {
      // ignore
    }
    sessions[role].isStreaming = false;
  }

  return {
    sessions,
    connect,
    disconnect,
    reconnect,
    send,
    steer,
    followUp,
    abort,
    refreshState,
    backfillHistory,
  };
}
