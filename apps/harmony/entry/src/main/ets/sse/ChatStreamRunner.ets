import { SseClient, SseHandle, SseStreamOpts } from './SseClient';
import { StreamEvent } from './StreamEvent';
import { StreamEventParser } from './StreamEventParser';
import { GatewayApi } from '../net/GatewayApi';
import { Attachment, HistoryItem, StartRunRequest } from '../net/dto/ChatDtos';

/** 空操作 StreamHandle，启动失败时返回。 */
class NoopStreamHandle implements StreamHandle {
  cancel(): void {}
}

/**
 * 双流编排 —— 复刻 Android sse/ChatStreamRunner.kt。
 *
 * - HERMES：POST /v1/runs 拿 run_id → GET /v1/runs/{id}/events SSE
 * - 非 HERMES：POST /v1/chat/completions SSE 流式读
 *
 * 两条流都通过 SseClient 获取原始 payload，再路由到 StreamEvent。
 *
 * ArkTS 无 Flow，用 callback 订阅式：调用方传 onEvent/onDone/onError。
 */
export interface StreamHandlers {
  onEvent: (e: StreamEvent) => void
  onDone: () => void
  onError: (e: Error) => void
}

export interface StreamHandle {
  cancel(): void
}

/** 非HERMES 流式发送的 messages 数组元素形态。 */
interface ChatCompletionMessage {
  role: string
  content: string
}

export class ChatStreamRunner {
  private sseClient: SseClient
  private gatewayApi: GatewayApi

  constructor(
    sseClient: SseClient,
    gatewayApi: GatewayApi,
  ) {
    this.sseClient = sseClient
    this.gatewayApi = gatewayApi
  }

  /**
   * 非HERMES 流式发送：POST /v1/chat/completions SSE。
   * @param messages 已有对话历史 [{role, content}]
   * @param attachments 本轮 user 附件（追加到最后一条 user message 上）
   */
  async streamChatCompletions(
    agentId: string,
    sessionId: string | null,
    engineType: string | null,
    model: string,
    messages: ChatCompletionMessage[],
    user: string | null,
    attachments: Attachment[] = [],
    handlers: StreamHandlers,
  ): Promise<StreamHandle> {
    const body = buildChatCompletionsBody(model, messages, user, attachments)
    const inner = await this.sseClient.stream({
      url: 'v1/chat/completions',
      method: 'POST',
      body,
      headers: { 'Content-Type': 'application/json' },
      onPayload: (payload) => {
        const e = StreamEventParser.parseNonHermes(payload)
        if (e !== null) {
          try { handlers.onEvent(e) } catch (err) {}
        }
      },
      onDone: () => {
        try { handlers.onEvent(StreamEvent.completed()) } catch (e) {}
        try { handlers.onDone() } catch (e) {}
      },
      onError: (e) => {
        try { handlers.onError(e) } catch (err) {}
      },
    })
    return inner
  }

  /**
   * HERMES 流式发送：先 POST /v1/runs 拿 run_id，再 GET /v1/runs/{id}/events SSE。
   * @param history 本轮之前的对话历史（user/assistant 消息，不含本轮 input）
   * @param attachments 本轮 input 的附件
   */
  async streamHermesRun(
    agentId: string,
    sessionId: string,
    engineType: string | null,
    model: string | null,
    input: string,
    history: HistoryItem[] = [],
    user: string | null = null,
    attachments: Attachment[] = [],
    handlers: StreamHandlers,
  ): Promise<StreamHandle> {
    const req = new StartRunRequest()
    req.session_id = sessionId
    req.input = input
    req.model = model
    req.conversation_history = history
    req.user = user
    req.attachments = attachments

    let runId: string
    try {
      const resp = await this.gatewayApi.startRun(req)
      runId = resp.run_id
    } catch (e) {
      const msg = e instanceof Error ? e.message : String(e)
      try { handlers.onEvent(StreamEvent.failed(`启动运行失败：${msg}`)) } catch (err) {}
      try { handlers.onDone() } catch (err) {}
      return new NoopStreamHandle()
    }
    try { handlers.onEvent(StreamEvent.runStarted(runId)) } catch (e) {}

    const inner = await this.sseClient.stream({
      url: `v1/runs/${runId}/events`,
      method: 'GET',
      onPayload: (payload) => {
        const e = StreamEventParser.parseHermes(payload)
        if (e !== null) {
          try { handlers.onEvent(e) } catch (err) {}
        }
      },
      onDone: () => {
        try { handlers.onDone() } catch (e) {}
      },
      onError: (e) => {
        try { handlers.onError(e) } catch (err) {}
      },
    })
    return inner
  }
}

/** 构造 chat-completions 请求体。对齐 Android ChatStreamRunner.buildChatCompletionsBody。 */
function buildChatCompletionsBody(
  model: string,
  messages: ChatCompletionMessage[],
  user: string | null,
  attachments: Attachment[],
): string {
  let lastUserIdx = -1
  for (let i = messages.length - 1; i >= 0; i--) {
    if (messages[i].role === 'user') { lastUserIdx = i; break }
  }
  const msgJson: string[] = []
  for (let i = 0; i < messages.length; i++) {
    const m = messages[i]
    if (i === lastUserIdx && attachments.length > 0) {
      const attsStr = attachmentsToJsonArray(attachments)
      msgJson.push(`{"role":${jsonStr(m.role)},"content":${jsonStr(m.content)},"attachments":${attsStr}}`)
    } else {
      msgJson.push(`{"role":${jsonStr(m.role)},"content":${jsonStr(m.content)}}`)
    }
  }
  const userPart = user !== null ? `,"user":${jsonStr(user)}` : ''
  return `{"model":${jsonStr(model)},"messages":[${msgJson.join(',')}],"stream":true,"stream_options":{"include_usage":true}${userPart}}`
}

function attachmentsToJsonArray(atts: Attachment[]): string {
  const parts: string[] = []
  for (const a of atts) {
    parts.push(`{"name":${jsonStr(a.name)},"path":${jsonStr(a.path)},"is_image":${a.is_image}}`)
  }
  return `[${parts.join(',')}]`
}

function jsonStr(s: string): string {
  return JSON.stringify(s)
}
