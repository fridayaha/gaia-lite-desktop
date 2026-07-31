import { AgentRepository } from '../../repo/AgentRepository';
import { ChatRepository } from '../../repo/ChatRepository';
import { MessageFeedbackRepository } from '../../repo/MessageFeedbackRepository';
import { SpeechRepository } from '../../repo/SpeechRepository';
import { SpeechPlayer } from '../../repo/SpeechPlayer';
import { AgentContext } from '../../net/AgentContext';
import { ChatStreamRunner, StreamHandlers, StreamHandle } from '../../sse/ChatStreamRunner';
import { StreamEvent } from '../../sse/StreamEvent';
import { isReasoningEchoOfReply } from '../../sse/ReasoningDedup';
import { StreamProbe } from '../../sse/StreamProbe';
import { ApiError } from '../../net/ManagerApi';
import { Message, Session, ToolCallState, HistoryItem, Attachment } from '../../net/dto/ChatDtos';
import { exportToJson, exportToTranscript } from '../../util/SessionExporter';
import fs from '@ohos.file.fs';
import { AccessibleAgent } from '../../net/dto/AgentDtos';
import { ChatUiState, ApprovalState } from './ChatUiState';
import { logW } from '../../util/Logger';

/** chat-completions 历史消息元素形态。 */
interface ChatCompletionMessage {
  role: string
  content: string
}

/**
 * 聊天 ViewModel。复刻 Android ui/chat/ChatViewModel.kt。
 *
 * Phase 0：bootstrap/switchSession/loadHistory/sendMessage/cancel/regenerate/submitApproval
 * Phase 2：setFeedback/submitDownFeedback/toggleFavorite/transcribeAudio/toggleSpeak/stopSpeak/toggleAutoSpeak
 */
export class ChatViewModel {
  private _state: ChatUiState = new ChatUiState()
  private listeners: Array<(s: ChatUiState) => void> = []
  private streamHandle: StreamHandle | null = null
  private streamingRunId: string | null = null
  private firstDeltaMarked = false
  private speechListener: (ref: string | null) => void = (ref) => {
    this.setState((s) => { s.speakingRef = ref })
  }
  private agentRepository: AgentRepository
  private chatRepository: ChatRepository
  private agentContext: AgentContext
  private chatStreamRunner: ChatStreamRunner
  private messageFeedbackRepository: MessageFeedbackRepository
  private speechRepository: SpeechRepository
  private speechPlayer: SpeechPlayer

  constructor(
    agentRepository: AgentRepository,
    chatRepository: ChatRepository,
    agentContext: AgentContext,
    chatStreamRunner: ChatStreamRunner,
    messageFeedbackRepository: MessageFeedbackRepository,
    speechRepository: SpeechRepository,
    speechPlayer: SpeechPlayer,
  ) {
    this.agentRepository = agentRepository
    this.chatRepository = chatRepository
    this.agentContext = agentContext
    this.chatStreamRunner = chatStreamRunner
    this.messageFeedbackRepository = messageFeedbackRepository
    this.speechRepository = speechRepository
    this.speechPlayer = speechPlayer
    this.speechPlayer.subscribe(this.speechListener)
  }

  get state(): ChatUiState {
    return this._state
  }

  subscribe(listener: (s: ChatUiState) => void): void {
    this.listeners.push(listener)
    listener(this._state)
  }

  unsubscribe(listener: (s: ChatUiState) => void): void {
    this.listeners = this.listeners.filter((l) => l !== listener)
  }

  private setState(patch: (s: ChatUiState) => void): void {
    patch(this._state)
    // 换新引用：ArkUI @State 仅在引用变更时触发整个 build() 重渲染。
    // 同引用的属性变更（如 streamingContent += delta）即使 @Observed 也不保证刷新顶层组件。
    this._state = this._state.snapshot()
    for (const l of this.listeners) {
      try { l(this._state) } catch (e) {}
    }
  }

  /** 入口：从路由参数 bootstrap。 */
  async bootstrap(agentId: string, engineType: string | null, sessionId: string | null): Promise<void> {
    this.setState((s) => {
      s.agentId = agentId
      s.engineType = engineType
      s.currentSessionId = sessionId
      s.bootstrapping = true
    })
    try {
      await this.loadAgentMeta(agentId, engineType)
      await this.ensureRunning(agentId)
      await this.loadSessions(true)
      await this.loadModels(agentId)
    } finally {
      this.setState((s) => { s.bootstrapping = false })
    }
  }

  private async loadAgentMeta(agentId: string, fallbackEngineType: string | null): Promise<void> {
    try {
      const agents = await this.agentRepository.getAccessibleAgents()
      const me = agents.find((a: AccessibleAgent) => a.id === agentId)
      if (me !== undefined) {
        this.setState((s) => { s.agentName = me.name; s.engineType = me.engine_type })
        this.agentContext.setAgent(agentId, me.engine_type)
      } else {
        this.agentContext.setAgent(agentId, fallbackEngineType)
      }
    } catch (e) {
      this.agentContext.setAgent(agentId, fallbackEngineType)
    }
  }

  private async ensureRunning(agentId: string): Promise<void> {
    try {
      let status = await this.agentRepository.getDeploymentStatus(agentId)
      this.setState((s) => { s.deployStatus = status.status })
      if (status.status === 'RUNNING') return
      await this.agentRepository.deploy(agentId)
      for (let i = 0; i < 30; i++) {
        await sleep(2000)
        status = await this.agentRepository.getDeploymentStatus(agentId)
        this.setState((s) => { s.deployStatus = status.status })
        if (status.status === 'RUNNING') return
        if (status.status === 'FAILED') {
          this.setState((s) => {
            s.engineAvailable = false
            s.deployErrorMessage = status.error_message ?? '引擎启动失败'
          })
          return
        }
      }
      this.setState((s) => {
        s.engineAvailable = false
        s.deployErrorMessage = '引擎启动超时'
      })
    } catch (e) {
      const msg = e instanceof Error ? e.message : '引擎不可用'
      this.setState((s) => {
        s.engineAvailable = false
        s.deployErrorMessage = msg
      })
    }
  }

  async loadSessions(autoResume: boolean = false): Promise<void> {
    const agentId = this._state.agentId
    if (agentId === null) return
    this.setState((s) => { s.loadingSessions = true })
    try {
      const list = await this.chatRepository.listSessions()
      list.sort((a: Session, b: Session) => {
        const aT = a.stableLastAt ?? a.stableCreatedAt
        const bT = b.stableLastAt ?? b.stableCreatedAt
        return bT - aT
      })
      this.setState((s) => { s.sessions = list; s.loadingSessions = false })
      if (autoResume && list.length > 0 && this._state.currentSessionId === null) {
        await this.switchSession(list[0].stableId)
      }
    } catch (e) {
      const msg = e instanceof Error ? e.message : '加载会话失败'
      this.setState((s) => { s.loadingSessions = false; s.error = msg })
    }
  }

  private async loadModels(agentId: string): Promise<void> {
    try {
      const resp = await this.agentRepository.getModels(agentId)
      const ids = resp.data.map((m) => m.id).filter((id) => id.length > 0)
      this.setState((s) => {
        s.models = ids
        if (s.currentModel === null && ids.length > 0) s.currentModel = ids[0]
      })
    } catch (e) {
      // 模型加载失败不阻塞主流程
    }
  }

  async switchSession(sessionId: string): Promise<void> {
    if (this._state.currentSessionId === sessionId && this._state.messages.length > 0) return
    this.speechPlayer.stop()
    this.setState((s) => {
      s.currentSessionId = sessionId
      s.messages = []
      s.error = null
    })
    this.agentContext.setSession(sessionId)
    await this.loadHistory(sessionId)
  }

  /** 会话抽屉开关（对齐 android openDrawer/closeDrawer）。 */
  openDrawer(): void {
    this.setState((s) => { s.drawerOpen = true })
  }

  closeDrawer(): void {
    this.setState((s) => { s.drawerOpen = false })
  }

  /**
   * 新建会话。对齐 android ChatViewModel.newSession：
   * DIFY 引擎首条消息才真建会话，本地先落一条占位 session。
   */
  async newSession(): Promise<void> {
    if (this._state.agentId === null) return
    this.speechPlayer.stop()
    const engineType = this._state.engineType
    if (engineType !== null && engineType.toUpperCase() === 'DIFY') {
      const localId = `local-${Date.now()}-${randomAlpha(6)}`
      const placeholder = new Session()
      placeholder.session_id = localId
      placeholder.title = '未开始'
      placeholder.created_at = Date.now() / 1000
      this.setState((s) => {
        s.sessions = [placeholder, ...s.sessions]
        s.currentSessionId = localId
        s.messages = []
        s.feedback = {}
        s.favorites = new Set<string>()
        s.drawerOpen = false
      })
      this.agentContext.setSession(localId)
      return
    }
    try {
      const session = await this.chatRepository.createSession(this._state.currentModel)
      this.setState((s) => {
        s.sessions = [session, ...s.sessions]
        s.currentSessionId = session.stableId
        s.messages = []
        s.feedback = {}
        s.favorites = new Set<string>()
        s.drawerOpen = false
      })
      this.agentContext.setSession(session.stableId)
    } catch (e) {
      this.setState((s) => { s.error = e instanceof Error ? e.message : '新建会话失败' })
    }
  }

  /** 重命名会话（乐观更新列表标题；失败落 error）。 */
  async renameSession(sessionId: string, newTitle: string): Promise<void> {
    const title = newTitle.trim()
    if (title.length === 0) return
    try {
      await this.chatRepository.updateTitle(sessionId, title)
      this.setState((s) => {
        s.sessions = s.sessions.map((it) => {
          if (it.stableId === sessionId) it.title = title
          return it
        })
      })
    } catch (e) {
      this.setState((s) => { s.error = e instanceof Error ? e.message : '重命名失败' })
    }
  }

  /** 删除会话；删的是当前会话则同时清空消息区（对齐 android deleteSession）。 */
  async deleteSession(sessionId: string): Promise<void> {
    try {
      await this.chatRepository.deleteSession(sessionId)
      const removedCurrent = this._state.currentSessionId === sessionId
      this.setState((s) => {
        s.sessions = s.sessions.filter((it) => it.stableId !== sessionId)
        if (removedCurrent) {
          s.currentSessionId = null
          s.messages = []
        }
      })
      if (removedCurrent) this.agentContext.setSession(null)
    } catch (e) {
      this.setState((s) => { s.error = e instanceof Error ? e.message : '删除会话失败' })
    }
  }

  // ── 批量删除（对齐 android ChatViewModel 批量区块） ──

  enterMultiSelect(initialId: string): void {
    this.setState((s) => {
      s.multiSelectMode = true
      s.selectedSessionIds = new Set<string>([initialId])
    })
  }

  toggleSessionSelected(id: string): void {
    this.setState((s) => {
      const next = new Set<string>(s.selectedSessionIds)
      if (next.has(id)) next.delete(id)
      else next.add(id)
      // 取消选中所有 → 自动退出批量模式
      if (next.size === 0) {
        s.multiSelectMode = false
        s.selectedSessionIds = new Set<string>()
      } else {
        s.selectedSessionIds = next
      }
    })
  }

  selectAllSessions(): void {
    this.setState((s) => {
      s.selectedSessionIds = new Set<string>(s.sessions.map((it) => it.stableId))
    })
  }

  cancelMultiSelect(): void {
    this.setState((s) => {
      s.multiSelectMode = false
      s.selectedSessionIds = new Set<string>()
    })
  }

  /** 顺序删除避免后端限流；删到当前会话则清空消息区（对齐 android deleteSelectedSessions）。 */
  async deleteSelectedSessions(): Promise<void> {
    const ids = Array.from(this._state.selectedSessionIds)
    if (ids.length === 0) return
    let failed = 0
    for (const id of ids) {
      try {
        await this.chatRepository.deleteSession(id)
      } catch (e) {
        failed++
      }
    }
    const wasCurrentDeleted = this._state.currentSessionId !== null
      && ids.indexOf(this._state.currentSessionId!) >= 0
    this.setState((s) => {
      s.sessions = s.sessions.filter((it) => ids.indexOf(it.stableId) < 0)
      if (wasCurrentDeleted) {
        s.currentSessionId = null
        s.messages = []
      }
      s.multiSelectMode = false
      s.selectedSessionIds = new Set<string>()
      if (failed > 0) s.error = `删除失败 ${failed} 条`
    })
    if (wasCurrentDeleted) this.agentContext.setSession(null)
  }

  /**
   * 把当前会话导出到用户通过系统保存面板选定的 uri。
   * isJson=true → JSON 格式；isJson=false → Markdown 转录（对齐 android writeExportToUri）。
   */
  async writeExportToUri(uri: string, isJson: boolean): Promise<void> {
    const found = this._state.sessions.find((it) => it.stableId === this._state.currentSessionId)
    const session: Session = found ?? new Session()
    const messages = this._state.messages
    try {
      const content = isJson ? exportToJson(session, messages) : exportToTranscript(session, messages)
      const f = fs.openSync(uri, fs.OpenMode.READ_WRITE | fs.OpenMode.CREATE | fs.OpenMode.TRUNC)
      try {
        fs.writeSync(f.fd, content)
      } finally {
        fs.closeSync(f)
      }
      this.setState((s) => { s.toast = `已导出 ${messages.length} 条消息` })
    } catch (e) {
      this.setState((s) => { s.toast = '导出失败，请重试' })
    }
  }

  /**
   * 清空当前会话：删旧会话 + 新建空会话（对齐 android clearConversation / web useChat）。
   */
  async clearConversation(): Promise<void> {
    const sid = this._state.currentSessionId
    if (sid === null) return
    if (this._state.agentId !== null) {
      try { await this.chatRepository.deleteSession(sid) } catch (e) {}
    }
    this.setState((s) => {
      s.sessions = s.sessions.filter((it) => it.stableId !== sid)
      s.currentSessionId = null
      s.messages = []
      s.streamingContent = ''
      s.thinkingText = ''
      s.toolCalls = []
      s.approvalPending = null
    })
    this.agentContext.setSession(null)
    // 立即新建一个空会话，让用户能直接开始新一轮对话
    await this.newSession()
  }

  private async loadHistory(sessionId: string): Promise<void> {
    this.setState((s) => { s.loadingMessages = true })
    try {
      const list = await this.chatRepository.listMessages(sessionId)
      this.setState((s) => { s.messages = list; s.loadingMessages = false })
      await this.loadFeedbackAndFavorites(sessionId)
    } catch (e) {
      const msg = e instanceof Error ? e.message : '加载消息失败'
      this.setState((s) => { s.loadingMessages = false; s.error = msg })
    }
  }

  private async loadFeedbackAndFavorites(sessionId: string): Promise<void> {
    try {
      const pair = await Promise.all([
        this.messageFeedbackRepository.listFeedback(sessionId),
        this.messageFeedbackRepository.listSessionFavoriteRefs(sessionId),
      ])
      const feedback = pair[0]
      const favs = pair[1]
      this.setState((s) => {
        s.feedback = feedback
        s.favorites = favs
      })
    } catch (e) {
      // 反馈/收藏恢复失败不阻塞主流程
    }
  }

  async sendMessage(text: string, attachments: Attachment[] = []): Promise<void> {
    const agentId = this._state.agentId
    const model = this._state.currentModel
    if (agentId === null || model === null) return
    const trimmed = text.trim()
    if (trimmed.length === 0 && attachments.length === 0) return
    let sessionId = this._state.currentSessionId
    if (sessionId === null) {
      const session = await this.chatRepository.createSession(null)
      sessionId = session.stableId
      this.setState((s) => {
        s.currentSessionId = sessionId!
        s.sessions = [session, ...s.sessions]
      })
      this.agentContext.setSession(sessionId)
    }
    await this.startStream(agentId, sessionId, this._state.engineType, model, trimmed, attachments)
  }

  private async startStream(
    agentId: string,
    sessionId: string,
    engineType: string | null,
    model: string,
    text: string,
    attachments: Attachment[] = [],
  ): Promise<void> {
    const localMsgId = Date.now()
    const userMsg = new Message()
    userMsg.id = localMsgId
    userMsg.role = 'user'
    userMsg.content = text
    // 立即落本地 user 消息（附件为含 localUri 的占位），UI 立刻可见缩略图/文件名。
    userMsg.attachments = attachments
    this.setState((s) => {
      s.messages = [...s.messages, userMsg]
      s.isStreaming = true
      s.streamingContent = ''
      s.thinkingText = ''
      s.toolCalls = []
      s.approvalPending = null
      s.error = null
      s.retryable = false
    })

    // 上传附件（后台并行）；失败则提示并不进入流式（对齐 android startStream 第 2 步）。
    let uploaded: Attachment[] = []
    if (attachments.length > 0) {
      try {
        uploaded = await Promise.all(attachments.map((a: Attachment) => {
          return this.chatRepository.uploadAttachment(agentId, a.name, a.mime ?? 'application/octet-stream', a.localUri ?? '')
        }))
      } catch (e) {
        this.setState((s) => {
          s.isStreaming = false
          s.toast = '附件上传失败，请重试'
        })
        return
      }
      // 回填真实 path 到本地 user 消息
      this.setState((s) => {
        s.messages = s.messages.map((m) => {
          if (m.id === localMsgId) m.attachments = uploaded
          return m
        })
      })
    }

    // 历史：含本轮 user 消息（chat-completions 协议要求 messages 以新 prompt 收尾）
    const history: ChatCompletionMessage[] = this._state.messages.map((m) => {
      const o: ChatCompletionMessage = { role: m.role, content: m.content ?? '' }
      return o
    })
    // Hermes history：排除本轮 user，仅可见消息
    const hermesHistory: HistoryItem[] = this._state.messages
      .slice(0, -1)
      .filter((m) => m.isVisible)
      .map((m) => {
        const h = new HistoryItem()
        h.role = m.role
        h.content = m.content ?? ''
        return h
      })

    this.firstDeltaMarked = false
    StreamProbe.begin(`engine=${engineType ?? '-'} model=${model} input=${text.length}ch hist=${hermesHistory.length}`)

    const handlers: StreamHandlers = {
      onEvent: (ev: StreamEvent) => {
        this.handleStreamEvent(ev, sessionId)
      },
      onDone: () => {
        // 流自然结束：若还在 streaming，落一条 assistant 消息
        if (this._state.isStreaming) {
          const content = this._state.streamingContent
          if (content.length > 0) {
            const msg = new Message()
            msg.role = 'assistant'
            msg.content = content
            msg.runId = this.streamingingRunIdSafe()
            this.setState((s) => {
              s.messages = [...s.messages, msg]
              s.isStreaming = false
              s.streamingContent = ''
              s.thinkingText = ''
              s.toolCalls = []
            })
            if (this._state.autoSpeak) {
              this.speechPlayer.speak(MessageFeedbackRepository.messageRefOf(msg), content)
            }
          } else {
            this.setState((s) => { s.isStreaming = false })
          }
        }
      },
      onError: (e: Error) => {
        StreamProbe.end(`error ${e.message.slice(0, 60)}`)
        const msg = new Message()
        msg.role = 'assistant'
        msg.content = `消息发送失败：${e.message}`
        this.setState((s) => {
          s.messages = [...s.messages, msg]
          s.isStreaming = false
          s.streamingContent = ''
          s.thinkingText = ''
          s.error = e.message
          s.retryable = true
        })
      },
    }

    try {
      const isHermes = engineType !== null && engineType.toUpperCase() === 'HERMES'
      if (isHermes) {
        this.streamHandle = await this.chatStreamRunner.streamHermesRun(
          agentId, sessionId, engineType, model, text, hermesHistory, null, uploaded, handlers,
        )
      } else {
        this.streamHandle = await this.chatStreamRunner.streamChatCompletions(
          agentId, sessionId, engineType, model, history, null, uploaded, handlers,
        )
      }
    } catch (e) {
      const msg = e instanceof Error ? e.message : String(e)
      logW('ChatViewModel', `startStream failed: ${msg}`)
      handlers.onError(e instanceof Error ? e : new Error(msg))
    }
  }

  private streamingingRunIdSafe(): string | null {
    return this.streamingRunId
  }

  private handleStreamEvent(ev: StreamEvent, sessionId: string): void {
    switch (ev.kind) {
      case 'Connected':
        break
      case 'ContentDelta':
        this.markFirstDeltaIfNeeded()
        StreamProbe.tickVm()
        this.setState((s) => { s.streamingContent += ev.text })
        break
      case 'ReasoningDelta':
        this.setState((s) => { s.thinkingText += ev.text })
        break
      case 'HermesDelta':
        this.markFirstDeltaIfNeeded()
        StreamProbe.tickVm()
        this.setState((s) => { s.streamingContent += ev.text })
        break
      case 'HermesReasoning': {
        const echo = isReasoningEchoOfReply(ev.text, this._state.streamingContent)
        StreamProbe.mark('REASONING', `len=${ev.text.length} echo=${echo}`)
        if (echo) return
        this.setState((s) => { s.thinkingText += ev.text })
        break
      }
      case 'RunStarted':
        StreamProbe.mark('RUN_STARTED', ev.runId)
        this.streamingRunId = ev.runId
        break
      case 'ToolStarted': {
        StreamProbe.mark('TOOL_START', ev.toolName)
        const tc = new ToolCallState(ev.toolName, ev.preview, ev.toolCallId, false, null)
        this.setState((s) => { s.toolCalls = [...s.toolCalls, tc] })
        break
      }
      case 'ToolCompleted': {
        StreamProbe.mark('TOOL_DONE', `${ev.toolName} err=${ev.error !== null}`)
        this.setState((s) => {
          s.toolCalls = s.toolCalls.map((t) => {
            if (t.name === ev.toolName && !t.completed) {
              return new ToolCallState(t.name, t.preview, t.toolCallId, true, ev.error, ev.result ?? t.result)
            }
            return t
          })
        })
        break
      }
      case 'ApprovalRequested':
        this.setState((s) => {
          s.approvalPending = new ApprovalState(ev.runId, ev.command, ev.description, ev.choices)
        })
        break
      case 'ApprovalResponded':
        this.setState((s) => {
          if (s.approvalPending !== null) {
            s.approvalPending.responded = true
            s.approvalPending.respondedChoice = ev.choice
            s.approvalPending.submitting = false
          }
        })
        setTimeout(() => {
          this.setState((s) => { s.approvalPending = null })
        }, 800)
        break
      case 'Completed': {
        StreamProbe.mark('COMPLETED', `streamed=${this._state.streamingContent.length}ch`)
        StreamProbe.end('completed')
        const content = this._state.streamingContent.length > 0
          ? this._state.streamingContent
          : (ev.output ?? '')
        const liveThinking = this._state.thinkingText.length > 0 ? this._state.thinkingText : null
        const liveToolCalls = this._state.toolCalls
        const newMsgs: Message[] = []
        if (content.length > 0) {
          const msg = new Message()
          msg.role = 'assistant'
          msg.content = content
          msg.runId = this.streamingRunId
          msg.liveThinking = liveThinking
          msg.liveToolCalls = liveToolCalls
          newMsgs.push(msg)
        }
        this.streamingRunId = null
        this.setState((s) => {
          s.messages = [...s.messages, ...newMsgs]
          s.isStreaming = false
          s.streamingContent = ''
          s.thinkingText = ''
          s.toolCalls = []
        })
        // 自动朗读：回复完成即播放（speak 会打断上一条，符合连续对话预期）
        if (newMsgs.length > 0 && this._state.autoSpeak && content.length > 0) {
          this.speechPlayer.speak(MessageFeedbackRepository.messageRefOf(newMsgs[0]), content)
        }
        // 新消息引擎 id 回填：历史响应的稳定自增 id 是反馈锚点（mid:{id}）的首选
        if (newMsgs.length > 0) {
          this.backfillMessageIds(sessionId)
        }
        break
      }
      case 'Failed':
        StreamProbe.end(`failed:${(ev.error ?? '').slice(0, 60)}`)
        this.setState((s) => {
          s.isStreaming = false
          s.error = ev.error
          s.retryable = true
        })
        break
      case 'Cancelled':
        StreamProbe.end('cancelled')
        this.setState((s) => {
          s.isStreaming = false
          s.streamingContent = ''
          s.thinkingText = ''
        })
        break
    }
  }

  /**
   * 流式完成后回填 assistant 消息的服务端自增 id（对齐 Android backfillMessageIds）。
   * 本地落的 assistant 消息 id=null，反馈锚点是 hash:{内容指纹}；冷启动从历史重载后
   * 同一条消息变成 mid:{服务端 id}，hash 锚点全部失效（收藏/赞踩显示丢失）。
   * 只按 role+content 从尾部对齐填充 id=null 的 assistant 消息。失败静默（hash 兜底）。
   */
  private async backfillMessageIds(sessionId: string): Promise<void> {
    let history: Message[]
    try {
      history = await this.chatRepository.listMessages(sessionId)
    } catch (e) {
      return
    }
    const uiMsgs = this._state.messages
    if (history.length === 0 || uiMsgs.length === 0) return
    // 尾部对齐：历史最后一条应与 UI 最后一条 assistant 对应
    const idByIndex = new Map<number, number>()
    let hi = history.length - 1
    let ui = uiMsgs.length - 1
    while (hi >= 0 && ui >= 0) {
      const um = uiMsgs[ui]
      if (um.role === 'assistant' && um.id === null) {
        // 在历史里从 hi 向前找内容匹配的 assistant
        let found = false
        let j = hi
        while (j >= 0) {
          const hm = history[j]
          if (hm.role === 'assistant' && hm.content === um.content && hm.id !== null) {
            idByIndex.set(ui, hm.id)
            hi = j - 1
            found = true
            break
          }
          j--
        }
        if (!found) break // 更早的本地消息与历史对不齐，停止（不动它们的 hash 锚点）
      }
      ui--
    }
    if (idByIndex.size === 0) return
    this.setState((s) => {
      s.messages = s.messages.map((m: Message, i: number) => {
        const nid = idByIndex.get(i)
        if (nid !== undefined) m.id = nid
        return m
      })
    })
  }

  private markFirstDeltaIfNeeded(): void {
    if (this.firstDeltaMarked) return
    this.firstDeltaMarked = true
    StreamProbe.mark('FIRST_DELTA')
  }

  /** 取消当前流式。 */
  cancel(): void {
    if (this.streamHandle !== null) {
      try { this.streamHandle.cancel() } catch (e) {}
      this.streamHandle = null
    }
    this.setState((s) => {
      s.isStreaming = false
      s.streamingContent = ''
      s.thinkingText = ''
      s.toolCalls = []
    })
    StreamProbe.end('cancel')
  }

  /** 重新生成最近一条 assistant 回复。 */
  async regenerate(): Promise<void> {
    // 找到最后一条 assistant 消息，删除，重发它对应的 user 消息
    const msgs = this._state.messages
    let lastAssistantIdx = -1
    for (let i = msgs.length - 1; i >= 0; i--) {
      if (msgs[i].role === 'assistant') { lastAssistantIdx = i; break }
    }
    if (lastAssistantIdx < 0) return
    // 找该 assistant 之前的 user 消息
    let userIdx = -1
    for (let i = lastAssistantIdx - 1; i >= 0; i--) {
      if (msgs[i].role === 'user') { userIdx = i; break }
    }
    if (userIdx < 0) return
    const userText = msgs[userIdx].content ?? ''
    // 删除从 userIdx 到末尾的所有消息
    this.setState((s) => {
      s.messages = s.messages.slice(0, userIdx)
    })
    await this.sendMessage(userText)
  }

  /** 提交审批。 */
  async submitApproval(choice: string): Promise<void> {
    const pending = this._state.approvalPending
    if (pending === null) return
    this.setState((s) => {
      if (s.approvalPending !== null) s.approvalPending.submitting = true
    })
    try {
      await this.chatRepository.submitApproval(pending.runId, choice)
    } catch (e) {
      const msg = e instanceof Error ? e.message : '审批提交失败'
      this.setState((s) => { s.error = msg })
    }
  }

  /**
   * 点赞 / 取消赞 / 取消踩。点踩（新值）不走这里——ChatScreen 弹窗收原因后调 submitDownFeedback。
   * 乐观更新 + 失败回滚。
   */
  async setFeedback(message: Message, rating: string): Promise<void> {
    const sid = this._state.currentSessionId
    const agentId = this._state.agentId
    if (sid === null || agentId === null) return
    const ref = MessageFeedbackRepository.messageRefOf(message)
    const current = this._state.feedback[ref]
    // 取消（同值再点）
    if (current === rating) {
      this.applyFeedback(ref, null)
      try {
        await this.messageFeedbackRepository.cancelFeedback(agentId, sid, ref)
      } catch (e) {
        this.applyFeedback(ref, current)
        this.setState((s) => { s.toast = '操作失败，请重试' })
      }
      return
    }
    // 仅点赞走这里
    if (rating !== 'up') return
    this.applyFeedback(ref, 'up')
    try {
      await this.messageFeedbackRepository.upsertFeedback(
        agentId, sid, ref, message.runId, 'up', null, null, message.content ?? '',
      )
    } catch (e) {
      this.applyFeedback(ref, current)
      this.setState((s) => { s.toast = '操作失败，请重试' })
    }
  }

  /** 点踩提交（ChatScreen 原因弹窗确定后调用）。reason 必填，comment 可选。 */
  async submitDownFeedback(message: Message, reason: string, comment: string | null): Promise<void> {
    const sid = this._state.currentSessionId
    const agentId = this._state.agentId
    if (sid === null || agentId === null) return
    const ref = MessageFeedbackRepository.messageRefOf(message)
    const current = this._state.feedback[ref]
    this.applyFeedback(ref, 'down')
    try {
      await this.messageFeedbackRepository.upsertFeedback(
        agentId, sid, ref, message.runId, 'down', reason, comment, message.content ?? '',
      )
    } catch (e) {
      this.applyFeedback(ref, current)
      this.setState((s) => { s.toast = '操作失败，请重试' })
    }
  }

  /** 收藏/取消收藏（乐观更新 + 失败回滚）。 */
  async toggleFavorite(message: Message): Promise<void> {
    const sid = this._state.currentSessionId
    const agentId = this._state.agentId
    if (sid === null || agentId === null) return
    const ref = MessageFeedbackRepository.messageRefOf(message)
    const favored = this._state.favorites.has(ref)
    this.applyFavorite(ref, !favored)
    try {
      if (favored) {
        await this.messageFeedbackRepository.removeFavorite(sid, ref)
      } else {
        await this.messageFeedbackRepository.addFavorite(
          agentId, sid, ref, message.runId, message.content ?? '',
        )
      }
    } catch (e) {
      this.applyFavorite(ref, favored)
      this.setState((s) => { s.toast = '操作失败，请重试' })
    }
  }

  private applyFeedback(ref: string, value: string | null): void {
    this.setState((s) => {
      const next: Record<string, string> = {}
      for (const k of Object.keys(s.feedback)) {
        if (k !== ref) next[k] = s.feedback[k]
      }
      if (value !== null) next[ref] = value
      s.feedback = next
    })
  }

  private applyFavorite(ref: string, favored: boolean): void {
    this.setState((s) => {
      const next = new Set<string>(s.favorites)
      if (favored) next.add(ref)
      else next.delete(ref)
      s.favorites = next
    })
  }

  /** 按住说话：录音字节 → gateway ASR → 识别文本。失败抛异常由调用方兜底。 */
  async transcribeAudio(audio: Uint8Array, format: string = 'm4a'): Promise<string | null> {
    try {
      const text = await this.speechRepository.transcribe(audio, format)
      if (text.length === 0) {
        this.setState((s) => { s.toast = '没有识别到内容，请重试' })
        return null
      }
      return text
    } catch (e) {
      this.setState((s) => { s.toast = '语音识别失败，请重试' })
      return null
    }
  }

  /** 历史消息图片附件：下载原始字节（无 localUri 时渲染缩略图用）。失败返回 null。 */
  async downloadAttachmentImage(path: string): Promise<ArrayBuffer | null> {
    const agentId = this._state.agentId
    if (agentId === null) return null
    try {
      return await this.chatRepository.downloadAttachmentBytes(agentId, path)
    } catch (e) {
      return null
    }
  }

  /** 朗读/停止朗读某条 assistant 消息（端侧 TTS）。 */
  toggleSpeak(message: Message): void {
    const ref = MessageFeedbackRepository.messageRefOf(message)
    this.speechPlayer.toggle(ref, message.content ?? '')
  }

  stopSpeak(): void {
    this.speechPlayer.stop()
  }

  /** 自动朗读开关：关闭时同时停掉当前播放。 */
  toggleAutoSpeak(): void {
    const on = !this._state.autoSpeak
    this.setState((s) => { s.autoSpeak = on })
    if (!on) this.speechPlayer.stop()
  }

  clearToast(): void {
    this.setState((s) => { s.toast = null })
  }

  /** 临时给 UI 显示一条 toast。Phase 2 用于占位提示（如语音输入需真机调试）。 */
  showToast(msg: string): void {
    this.setState((s) => { s.toast = msg })
  }

  /** 模型选择面板。 */
  openModelSheet(): void {
    this.setState((s) => { s.modelSheetOpen = true })
  }

  closeModelSheet(): void {
    this.setState((s) => { s.modelSheetOpen = false })
  }

  selectModel(id: string): void {
    this.setState((s) => {
      s.currentModel = id
      s.modelSheetOpen = false
    })
  }
}

function sleep(ms: number): Promise<void> {
  return new Promise((resolve) => setTimeout(resolve, ms))
}

/** DIFY 本地占位会话 id 的随机后缀（对齐 android newSession 的 6 位小写字母）。 */
function randomAlpha(n: number): string {
  const chars = 'abcdefghijklmnopqrstuvwxyz'
  let out = ''
  for (let i = 0; i < n; i++) {
    out += chars.charAt(Math.floor(Math.random() * 26))
  }
  return out
}
