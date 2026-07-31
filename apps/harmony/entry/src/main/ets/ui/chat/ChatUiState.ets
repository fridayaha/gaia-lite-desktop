import { Message, ToolCallState, Session } from '../../net/dto/ChatDtos';

/**
 * 审批批处理 UI 状态。复刻 Android ui/chat/ChatUiState.kt ApprovalState。
 */
export class ApprovalState {
  runId: string = ''
  command: string = ''
  description: string = ''
  choices: string[] = []
  submitting: boolean = false
  responded: boolean = false
  respondedChoice: string | null = null

  constructor(
    runId: string,
    command: string,
    description: string,
    choices: string[],
  ) {
    this.runId = runId
    this.command = command
    this.description = description
    this.choices = choices
  }
}

/**
 * 聊天页 UI 状态。复刻 Android ui/chat/ChatUiState.kt。
 * Phase 0 仅核心字段；feedback/favorites/speaking/autoSpeak/developerMode/multiSelect 留 Phase 1/2。
 *
 * @Observed 必加：ArkUI @State 对同一引用的对象的属性变更（如 streamingContent += delta）
 * 默认不触发重渲染，需要 @Observed 让 ArkUI 监听属性级变更。流式逐字刷新全靠它。
 */
@Observed
export class ChatUiState {
  agentId: string | null = null
  agentName: string | null = null
  engineType: string | null = null
  deployStatus: string | null = null
  deployErrorMessage: string | null = null
  engineAvailable: boolean = true
  bootstrapping: boolean = true
  sessions: Session[] = []
  currentSessionId: string | null = null
  messages: Message[] = []
  loadingSessions: boolean = false
  loadingMessages: boolean = false
  models: string[] = []
  currentModel: string | null = null
  modelSheetOpen: boolean = false
  drawerOpen: boolean = false
  isStreaming: boolean = false
  streamingContent: string = ''
  thinkingText: string = ''
  toolCalls: ToolCallState[] = []
  approvalPending: ApprovalState | null = null
  pendingRuns: object[] = []
  feedback: Record<string, string> = {}
  favorites: Set<string> = new Set<string>()
  speakingRef: string | null = null
  autoSpeak: boolean = false
  pendingScrollIndex: number | null = null
  pendingScrollOffset: number | null = null
  error: string | null = null
  retryable: boolean = false
  toast: string | null = null
  reconnecting: boolean = false
  reconnectAttempt: number = 0
  developerMode: boolean = false
  multiSelectMode: boolean = false
  selectedSessionIds: Set<string> = new Set<string>()

  /**
   * 浅拷贝当前 state，返回新引用。
   * ArkUI @State 仅在引用变更时重渲染整个组件树；同一引用的属性变更（streamingContent += delta）
   * 即使有 @Observed 也无法保证触发顶层 build() 重新执行。流式逐字刷新靠每次通知换新引用。
   */
  snapshot(): ChatUiState {
    const s = new ChatUiState()
    s.agentId = this.agentId
    s.agentName = this.agentName
    s.engineType = this.engineType
    s.deployStatus = this.deployStatus
    s.deployErrorMessage = this.deployErrorMessage
    s.engineAvailable = this.engineAvailable
    s.bootstrapping = this.bootstrapping
    s.sessions = this.sessions
    s.currentSessionId = this.currentSessionId
    s.messages = this.messages
    s.loadingSessions = this.loadingSessions
    s.loadingMessages = this.loadingMessages
    s.models = this.models
    s.currentModel = this.currentModel
    s.modelSheetOpen = this.modelSheetOpen
    s.drawerOpen = this.drawerOpen
    s.isStreaming = this.isStreaming
    s.streamingContent = this.streamingContent
    s.thinkingText = this.thinkingText
    s.toolCalls = this.toolCalls
    s.approvalPending = this.approvalPending
    s.pendingRuns = this.pendingRuns
    s.feedback = this.feedback
    s.favorites = this.favorites
    s.speakingRef = this.speakingRef
    s.autoSpeak = this.autoSpeak
    s.pendingScrollIndex = this.pendingScrollIndex
    s.pendingScrollOffset = this.pendingScrollOffset
    s.error = this.error
    s.retryable = this.retryable
    s.toast = this.toast
    s.reconnecting = this.reconnecting
    s.reconnectAttempt = this.reconnectAttempt
    s.developerMode = this.developerMode
    s.multiSelectMode = this.multiSelectMode
    s.selectedSessionIds = this.selectedSessionIds
    return s
  }
}
