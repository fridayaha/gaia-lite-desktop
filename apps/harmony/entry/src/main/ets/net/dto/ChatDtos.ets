/**
 * 聊天会话 / 消息 DTO。复刻 Android net/dto/ChatDtos.kt。
 * Message 含客户端侧元数据 runId/liveThinking/liveToolCalls，运行期写入、不参与序列化
 * （JSON.stringify 默认会带上这些字段，但 ChatRepository 里只在落库前调用纯净字段；
 * 这里保留字段以最小化 Android 端 API 兼容差异）。
 */

export class Session {
  session_id: string | null = null
  id: string | null = null
  title: string | null = null
  created_at: number | null = null
  started_at: number | null = null
  updated_at: number | null = null
  last_message_at: number | null = null
  model: string | null = null
  preview: string | null = null

  static fromJson(o: Record<string, Object>): Session {
    const r = new Session()
    if (typeof o['session_id'] === 'string') r.session_id = o['session_id']
    if (typeof o['id'] === 'string') r.id = o['id']
    if (typeof o['title'] === 'string') r.title = o['title']
    if (typeof o['created_at'] === 'number') r.created_at = o['created_at']
    if (typeof o['started_at'] === 'number') r.started_at = o['started_at']
    if (typeof o['updated_at'] === 'number') r.updated_at = o['updated_at']
    if (typeof o['last_message_at'] === 'number') r.last_message_at = o['last_message_at']
    if (typeof o['model'] === 'string') r.model = o['model']
    if (typeof o['preview'] === 'string') r.preview = o['preview']
    return r
  }

  get stableId(): string {
    return this.session_id ?? this.id ?? ''
  }

  get stableTitle(): string {
    const t = this.title ?? ''
    if (t.length > 0) return t
    const p = this.preview ?? ''
    if (p.length > 0) return p
    return '未命名'
  }

  get stableCreatedAt(): number {
    return this.created_at ?? this.started_at ?? 0
  }

  get stableLastAt(): number | null {
    return this.last_message_at ?? this.updated_at ?? this.started_at ?? null
  }
}

export class CreateSessionRequest {
  model: string | null = null
}

export class CreateSessionResponse {
  session: Session | null = null

  static fromJson(o: Record<string, Object>): CreateSessionResponse {
    const r = new CreateSessionResponse()
    const s = o['session']
    if (s instanceof Object) {
      r.session = Session.fromJson(s as Record<string, Object>)
    }
    return r
  }
}

export class UpdateTitleRequest {
  title: string = ''
}

export class SessionListResponse {
  sessions: Session[] | null = null
  data: Session[] | null = null

  static fromJson(o: Record<string, Object>): SessionListResponse {
    const r = new SessionListResponse()
    const sessions = o['sessions']
    if (sessions instanceof Array) {
      r.sessions = sessions.filter((v: Object) => v instanceof Object)
        .map((v: Object) => Session.fromJson(v as Record<string, Object>))
    }
    const data = o['data']
    if (data instanceof Array) {
      r.data = data.filter((v: Object) => v instanceof Object)
        .map((v: Object) => Session.fromJson(v as Record<string, Object>))
    }
    return r
  }
}

export class ToolFunction {
  name: string | null = null
  arguments: string | null = null

  static fromJson(o: Record<string, Object>): ToolFunction {
    const r = new ToolFunction()
    if (typeof o['name'] === 'string') r.name = o['name']
    if (typeof o['arguments'] === 'string') r.arguments = o['arguments']
    return r
  }
}

export class ToolCall {
  id: string | null = null
  type: string | null = null
  function: ToolFunction | null = null

  static fromJson(o: Record<string, Object>): ToolCall {
    const r = new ToolCall()
    if (typeof o['id'] === 'string') r.id = o['id']
    if (typeof o['type'] === 'string') r.type = o['type']
    const fn = o['function']
    if (fn instanceof Object) {
      r.function = ToolFunction.fromJson(fn as Record<string, Object>)
    }
    return r
  }
}

export class Attachment {
  name: string = ''
  path: string = ''
  is_image: boolean = false
  mime: string | null = null
  size: number | null = null
  thumbnail_url: string | null = null
  /** 本地 content Uri，仅用于聊天框/Composer 内即时预览；不参与 gateway 序列化。 */
  localUri: string | null = null

  static fromJson(o: Record<string, Object>): Attachment {
    const r = new Attachment()
    if (typeof o['name'] === 'string') r.name = o['name']
    if (typeof o['path'] === 'string') r.path = o['path']
    if (typeof o['is_image'] === 'boolean') r.is_image = o['is_image']
    if (typeof o['mime'] === 'string') r.mime = o['mime']
    if (typeof o['size'] === 'number') r.size = o['size']
    if (typeof o['thumbnail_url'] === 'string') r.thumbnail_url = o['thumbnail_url']
    if (typeof o['localUri'] === 'string') r.localUri = o['localUri']
    return r
  }
}

/** 一次工具调用的 UI 快照（流式 ToolStarted/ToolCompleted 增量更新）。 */
export class ToolCallState {
  name: string = ''
  preview: string = ''
  toolCallId: string | null = null
  completed: boolean = false
  error: string | null = null
  result: string | null = null

  constructor(
    name: string = '',
    preview: string = '',
    toolCallId: string | null = null,
    completed: boolean = false,
    error: string | null = null,
    result: string | null = null,
  ) {
    this.name = name
    this.preview = preview
    this.toolCallId = toolCallId
    this.completed = completed
    this.error = error
    this.result = result
  }
}

export class Message {
  id: number | null = null
  role: string = ''
  content: string | null = null
  tool_calls: ToolCall[] = []
  provider_details: string | null = null
  name: string | null = null
  attachments: Attachment[] = []
  /** 客户端侧：本次回复对应的引擎 run_id；历史消息响应无此字段。 */
  runId: string | null = null
  /** 客户端侧：本次回复的中间过程快照（思考 + 工具调用）。 */
  liveThinking: string | null = null
  /** 客户端侧：工具调用 UI 快照列表。 */
  liveToolCalls: ToolCallState[] = []

  static fromJson(o: Record<string, Object>): Message {
    const r = new Message()
    if (typeof o['id'] === 'number') r.id = o['id']
    if (typeof o['role'] === 'string') r.role = o['role']
    if (typeof o['content'] === 'string') r.content = o['content']
    if (typeof o['provider_details'] === 'string') r.provider_details = o['provider_details']
    if (typeof o['name'] === 'string') r.name = o['name']
    const tcs = o['tool_calls']
    if (tcs instanceof Array) {
      r.tool_calls = tcs.filter((v: Object) => v instanceof Object)
        .map((v: Object) => ToolCall.fromJson(v as Record<string, Object>))
    }
    const atts = o['attachments']
    if (atts instanceof Array) {
      r.attachments = atts.filter((v: Object) => v instanceof Object)
        .map((v: Object) => Attachment.fromJson(v as Record<string, Object>))
    }
    return r
  }

  get isVisible(): boolean {
    const isTool = this.role === 'tool'
    const isAssistantEmptyWithTools = this.role === 'assistant'
      && (this.content === null || this.content.length === 0)
      && this.tool_calls.length > 0
    return !isTool && !isAssistantEmptyWithTools
  }
}

export class MessageListResponse {
  messages: Message[] | null = null
  data: Message[] | null = null

  static fromJson(o: Record<string, Object>): MessageListResponse {
    const r = new MessageListResponse()
    const msgs = o['messages']
    if (msgs instanceof Array) {
      r.messages = msgs.filter((v: Object) => v instanceof Object)
        .map((v: Object) => Message.fromJson(v as Record<string, Object>))
    }
    const data = o['data']
    if (data instanceof Array) {
      r.data = data.filter((v: Object) => v instanceof Object)
        .map((v: Object) => Message.fromJson(v as Record<string, Object>))
    }
    return r
  }
}

export class HistoryItem {
  role: string = ''
  content: string = ''
}

export class StartRunRequest {
  session_id: string = ''
  input: string = ''
  model: string | null = null
  /**
   * Hermes 单 run 无状态：必须把先前 user/assistant 消息显式塞进请求体，
   * 否则引擎只看到本轮 input，"上面那个" 之类指代会因无上下文而答非所问。
   * 不含本轮刚追加的 user 消息（本轮 input 由 [input] 字段单独传）。
   */
  conversation_history: HistoryItem[] = []
  user: string | null = null
  attachments: Attachment[] = []
}

export class StartRunResponse {
  run_id: string = ''

  static fromJson(o: Record<string, Object>): StartRunResponse {
    const r = new StartRunResponse()
    if (typeof o['run_id'] === 'string') r.run_id = o['run_id']
    return r
  }
}

export class RunStatusResponse {
  run_id: string | null = null
  status: string | null = null
  error: string | null = null

  static fromJson(o: Record<string, Object>): RunStatusResponse {
    const r = new RunStatusResponse()
    if (typeof o['run_id'] === 'string') r.run_id = o['run_id']
    if (typeof o['status'] === 'string') r.status = o['status']
    if (typeof o['error'] === 'string') r.error = o['error']
    return r
  }
}

export class ApprovalRequest {
  choice: string = '' // once / session / always / deny
}

export class ApprovalResponse {
  run_id: string | null = null
  status: string | null = null
  choice: string | null = null

  static fromJson(o: Record<string, Object>): ApprovalResponse {
    const r = new ApprovalResponse()
    if (typeof o['run_id'] === 'string') r.run_id = o['run_id']
    if (typeof o['status'] === 'string') r.status = o['status']
    if (typeof o['choice'] === 'string') r.choice = o['choice']
    return r
  }
}

export class UploadFileResponse {
  filename: string | null = null
  path: string | null = null
  size: number | null = null
  mime: string | null = null
  is_image: boolean = false

  static fromJson(o: Record<string, Object>): UploadFileResponse {
    const r = new UploadFileResponse()
    if (typeof o['filename'] === 'string') r.filename = o['filename']
    if (typeof o['path'] === 'string') r.path = o['path']
    if (typeof o['size'] === 'number') r.size = o['size']
    if (typeof o['mime'] === 'string') r.mime = o['mime']
    if (typeof o['is_image'] === 'boolean') r.is_image = o['is_image']
    return r
  }
}
