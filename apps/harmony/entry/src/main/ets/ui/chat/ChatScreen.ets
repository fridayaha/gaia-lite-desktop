import { ChatViewModel } from './ChatViewModel';
import { ChatUiState, ApprovalState } from './ChatUiState';
import { AppContainer } from '../../di/AppContainer';
import { AppRouter, Routes } from '../nav/Routes';
import { LightTheme } from '../theme/Colors';
import { Typography } from '../theme/Typography';
import { IconPaths, MIcon } from '../theme/Icons';
import { Composer } from './Composer';
import { AttachmentGallery } from './components/AttachmentGallery';
import { MarkdownText } from './components/MarkdownText';
import { ThinkingCard } from './components/ThinkingCard';
import { ToolCard } from './components/ToolCard';
import { ApprovalCard } from './components/ApprovalCard';
import { SessionDrawer } from './SessionDrawer';
import { Message, ToolCallState, Attachment } from '../../net/dto/ChatDtos';
import { MessageFeedbackRepository } from '../../repo/MessageFeedbackRepository';
import { safeFilename } from '../../util/SessionExporter';
import { pasteboard } from '@kit.BasicServicesKit';
import { picker } from '@kit.CoreFileKit';
import image from '@ohos.multimedia.image';

/**
 * 聊天主屏。复刻 Android ui/chat/ChatScreen.kt 的核心结构。
 * 顶部 agent 名 + 退出按钮；中间消息列表（含 streaming/思考/工具/审批）；
 * 底部 Composer 输入栏。
 * 滚动 effect：流式跟到底部（对标 ChatScreen.kt:177-219 Effect 1+2）。
 */

@Component
export struct ChatScreen {
  @Consume('container') container: AppContainer
  @State state: ChatUiState = new ChatUiState()
  @Prop agentId: string = ''
  @State showClearConfirm: boolean = false
  @State viewerOpen: boolean = false
  @State viewerUri: string | null = null
  @State viewerPm: image.PixelMap | null = null
  private vm: ChatViewModel | null = null
  private listener: (s: ChatUiState) => void = (s) => {
    const prevMessages = this.state.messages.length
    const prevStreaming = this.state.isStreaming
    this.state = s
    if (s.toast !== null && s.toast.length > 0) {
      this.getUIContext().getPromptAction().showToast({ message: s.toast, duration: 2000 })
      this.vm?.clearToast()
    }
    this.followScroll(s, prevMessages, prevStreaming)
  }
  private listScroller: Scroller = new Scroller()
  private lastContentLen: number = 0
  private lastFollowAt: number = 0
  private followTimer: number | null = null
  // 压缩前是否贴底（onDidScroll 持续采样）：IME 弹起只在该值为真时补钉
  private wasAtBottom: boolean = true
  // 上次状态里的会话 id：侦测会话切换（冷启动恢复/抽屉切换）触发钉底
  private lastSessionId: string = ''

  aboutToAppear(): void {
    this.vm = new ChatViewModel(
      this.container.agentRepository,
      this.container.chatRepository,
      this.container.agentContext,
      this.container.chatStreamRunner,
      this.container.messageFeedbackRepository,
      this.container.speechRepository,
      this.container.speechPlayer,
    )
    this.vm.subscribe(this.listener)
    this.container.speechPlayer.onUnavailable = () => {
      this.getUIContext().getPromptAction().showToast({ message: '语音引擎不可用', duration: 3000 })
    }
    const param = AppRouter.instance().currentParam()
    const agentId = param['agentId'] ?? this.agentId
    const engineType = param['engineType'] ?? ''
    const sessionId = param['sessionId'] ?? ''
    this.vm.bootstrap(agentId, engineType, sessionId.length > 0 ? sessionId : null)
  }

  aboutToDisappear(): void {
    if (this.vm !== null) this.vm.unsubscribe(this.listener)
    this.vm?.cancel()
  }

  /**
   * 流式跟随钉底（对齐 android ChatScreen Effect 2：throttleLatest(200) + 瞬时滚动，不用动画）。
   * leading 200ms 节流 + trailing 补滚；滚动延迟 32ms 等新一帧布局完成，避免钉到旧底部。
   * 流结束/新消息落地：立即钉底 + 100ms 后补钉一次（对齐 Effect 1 scrollToItem+scrollBy(MAX)）。
   */
  followScroll(s: ChatUiState, prevMessages: number, prevStreaming: boolean): void {
    // 会话切换（冷启动恢复/抽屉切换）必钉底。长 Markdown 首帧后高度还会再长，
    // 单次 scrollEdge 容易钉在中途（冷启动落在长回复中段就是这么来的），多次补钉覆盖。
    const sid = s.currentSessionId ?? ''
    if (sid !== this.lastSessionId) {
      this.lastSessionId = sid
      this.wasAtBottom = true
      this.pinBottomWithRetry()
    }
    const contentLen = s.streamingContent.length + s.thinkingText.length
    if (s.isStreaming || contentLen > 0) {
      if (contentLen > 0 && contentLen !== this.lastContentLen) {
        const now = Date.now()
        const elapsed = now - this.lastFollowAt
        if (elapsed >= 200) {
          this.lastFollowAt = now
          this.scrollBottomDeferred()
        } else if (this.followTimer === null) {
          this.followTimer = setTimeout(() => {
            this.followTimer = null
            this.lastFollowAt = Date.now()
            this.scrollBottomDeferred()
          }, 200 - elapsed)
        }
      }
      this.lastContentLen = contentLen
      return
    }
    this.lastContentLen = 0
    const streamJustEnded = prevStreaming && !s.isStreaming
    if (s.messages.length !== prevMessages || streamJustEnded) {
      this.pinBottomWithRetry()
    }
  }

  private scrollBottomDeferred(): void {
    setTimeout(() => { this.listScroller.scrollEdge(Edge.Bottom) }, 32)
  }

  /** 钉底 + 延时补钉：等 List 首帧/内容长高完成后再钉，幂等。 */
  private pinBottomWithRetry(): void {
    const delays: number[] = [32, 150, 400]
    for (const d of delays) {
      setTimeout(() => { this.listScroller.scrollEdge(Edge.Bottom) }, d)
    }
  }

  /** 会话抽屉开/关（250ms 滑入滑出，对齐 android ModalNavigationDrawer）。 */
  openDrawerAnimated(): void {
    animateTo({ duration: 250, curve: Curve.EaseOut }, () => { this.vm?.openDrawer() })
  }

  closeDrawerAnimated(): void {
    animateTo({ duration: 250, curve: Curve.EaseIn }, () => { this.vm?.closeDrawer() })
  }

  /** 历史消息图片附件下载（AttachmentGallery 回调）。 */
  private fetchAttachment(path: string): Promise<ArrayBuffer | null> {
    const vm = this.vm
    if (vm === null) return Promise.resolve(null)
    return vm.downloadAttachmentImage(path)
  }

  /** 打开全屏图片预览（本地 localUri 或已下载的 PixelMap）。 */
  openViewer(uri: string | null, pm: image.PixelMap | null): void {
    if (uri === null && pm === null) return
    this.viewerUri = uri
    this.viewerPm = pm
    this.viewerOpen = true
  }

  /** 复制消息内容到剪贴板。 */
  copyMessage(content: string): void {
    const pb = pasteboard.getSystemPasteboard()
    const data = pasteboard.createData(pasteboard.MIMETYPE_TEXT_PLAIN, content)
    pb.setData(data).then(() => {
      this.vm?.showToast('已复制')
    })
  }

  /** 系统保存面板导出当前会话（对齐 android SAF CreateDocument 流程）。 */
  async exportSession(isJson: boolean): Promise<void> {
    const s = this.state.sessions.find((it) => it.stableId === this.state.currentSessionId)
    const title = s?.stableTitle ?? 'session'
    const name = isJson
      ? `session-${safeFilename(title, 'session')}.json`
      : `transcript-${safeFilename(title, 'session')}.md`
    try {
      const options = new picker.DocumentSaveOptions()
      options.newFileNames = [name]
      const documentPicker = new picker.DocumentViewPicker(getContext(this))
      const uris = await documentPicker.save(options)
      if (uris.length === 0) return // 用户取消
      await this.vm?.writeExportToUri(uris[0], isJson)
    } catch (e) {
      this.vm?.showToast('导出失败，请重试')
    }
  }

  /** 跳转到该 assistant 消息对应的用户提问。 */
  jumpToQuestion(idx: number): void {
    for (let i = idx - 1; i >= 0; i--) {
      if (this.state.messages[i].role === 'user') {
        this.listScroller.scrollToIndex(i)
        return
      }
    }
  }

  /** 最后一条 assistant 消息下标；无则 -1。 */
  lastAssistantIndex(): number {
    for (let i = this.state.messages.length - 1; i >= 0; i--) {
      if (this.state.messages[i].role === 'assistant') return i
    }
    return -1
  }

  showStreamingBubble(): boolean {
    return this.state.isStreaming || this.state.streamingContent.length > 0
  }

  @Builder
  messageItem(msg: Message, idx: number): void {
    if (msg.role === 'user') {
      // 用户消息：右对齐气泡
      Column() {
        if (msg.liveThinking !== null && msg.liveThinking.length > 0) {
          ThinkingCard({ text: msg.liveThinking })
            .margin({ bottom: 6 })
        }
        ForEach(msg.liveToolCalls, (tc: ToolCallState, tcIdx: number) => {
          ToolCard({ tool: tc })
            .margin({ bottom: 6 })
        }, (tc: ToolCallState, tcIdx: number) => `${tcIdx}-${tc.name}`)
        if (msg.attachments.length > 0 || (msg.content !== null && msg.content.length > 0)) {
          Column() {
            if (msg.attachments.length > 0) {
              AttachmentGallery({
                attachments: msg.attachments,
                onFetch: (path: string): Promise<ArrayBuffer | null> => this.fetchAttachment(path),
                onView: (a: Attachment, pm: image.PixelMap | null) => { this.openViewer(a.localUri, pm) },
              })
                .margin({ bottom: 6 })
            }
            if (msg.content !== null && msg.content.length > 0) {
              Text(msg.content)
                .fontSize(Typography.bodyMedium)
                .fontColor(LightTheme.onAccent)
            }
          }
          .alignItems(HorizontalAlign.End)
          .backgroundColor(LightTheme.accent)
          .borderRadius({ topLeft: 16, topRight: 16, bottomLeft: 16, bottomRight: 4 })
          .padding({ left: 14, right: 14, top: 10, bottom: 10 })
          .constraintSize({ maxWidth: '100%' })
        }
      }
      .alignItems(HorizontalAlign.End)
      .width('100%')
      .padding({ left: 16, right: 16, top: 6, bottom: 6 })
    } else if (msg.role === 'assistant') {
      // AI 消息：左对齐气泡
      Column() {
        if (msg.liveThinking !== null && msg.liveThinking.length > 0) {
          ThinkingCard({ text: msg.liveThinking })
            .margin({ bottom: 6 })
        }
        ForEach(msg.liveToolCalls, (tc: ToolCallState, tcIdx: number) => {
          ToolCard({ tool: tc })
            .margin({ bottom: 6 })
        }, (tc: ToolCallState, tcIdx: number) => `${tcIdx}-${tc.name}`)
        if (msg.attachments.length > 0) {
          AttachmentGallery({
            attachments: msg.attachments,
            onFetch: (path: string): Promise<ArrayBuffer | null> => this.fetchAttachment(path),
            onView: (a: Attachment, pm: image.PixelMap | null) => { this.openViewer(a.localUri, pm) },
          })
            .margin({ bottom: 6 })
        }
        if (msg.content !== null && msg.content.length > 0) {
          MarkdownText({ src: msg.content })
            .constraintSize({ maxWidth: '100%' })
            .backgroundColor(LightTheme.surface)
            .borderRadius({ topLeft: 16, topRight: 16, bottomLeft: 4, bottomRight: 16 })
            .padding({ left: 14, right: 14, top: 10, bottom: 10 })
        }
        // 反馈/收藏/朗读 行：仅最后一条 assistant 且非流式时显示（对齐 android isLastAssistant）
        if (!this.showStreamingBubble() && idx === this.lastAssistantIndex()) {
          this.messageActions(msg, idx)
        }
      }
      .alignItems(HorizontalAlign.Start)
      .width('100%')
      .padding({ left: 16, right: 16, top: 6, bottom: 6 })
    }
  }

  @Builder
  messageActions(msg: Message, idx: number): void {
    Column() {
      Divider()
        .color(LightTheme.borderVariant)
        .strokeWidth(0.5)
      Row() {
        this.actionButton(IconPaths.arrowUpward, LightTheme.muted, () => { this.jumpToQuestion(idx) })
        this.actionButton(
          IconPaths.thumbUp,
          this.state.feedback[MessageFeedbackRepository.messageRefOf(msg)] === 'up' ? LightTheme.accent : LightTheme.muted,
          () => { this.vm?.setFeedback(msg, 'up') },
        )
        this.actionButton(
          IconPaths.thumbDown,
          this.state.feedback[MessageFeedbackRepository.messageRefOf(msg)] === 'down' ? LightTheme.error : LightTheme.muted,
          () => { /* 点踩弹窗：Phase 3 接入 */ },
        )
        this.actionButton(
          IconPaths.star,
          this.state.favorites.has(MessageFeedbackRepository.messageRefOf(msg)) ? LightTheme.accent : LightTheme.muted,
          () => { this.vm?.toggleFavorite(msg) },
        )
        this.actionButton(
          IconPaths.volumeUp,
          this.state.speakingRef === MessageFeedbackRepository.messageRefOf(msg) ? LightTheme.accent : LightTheme.muted,
          () => { this.vm?.toggleSpeak(msg) },
        )
        this.actionButton(IconPaths.contentCopy, LightTheme.muted, () => { this.copyMessage(msg.content ?? '') })
      }
      .padding({ top: 4 })
    }
    .width('100%')
    .padding({ top: 6 })
    .alignItems(HorizontalAlign.Start)
  }

  @Builder
  actionButton(icon: string, color: number, onTap: () => void): void {
    Row() {
      MIcon({ path: icon, iconSize: 14, color: color })
    }
    .width(28)
    .height(28)
    .alignItems(VerticalAlign.Center)
    .justifyContent(FlexAlign.Center)
    .margin({ right: 2 })
    .onClick(onTap)
  }

  @Builder
  streamingItem(): void {
    if (this.state.isStreaming) {
      Column() {
        if (this.state.thinkingText.length > 0) {
          ThinkingCard({ text: this.state.thinkingText })
            .margin({ bottom: 6 })
        }
        ForEach(this.state.toolCalls, (tc: ToolCallState, tcIdx: number) => {
          ToolCard({ tool: tc })
            .margin({ bottom: 6 })
        }, (tc: ToolCallState, tcIdx: number) => `${tcIdx}-${tc.name}`)
        if (this.state.approvalPending !== null) {
          ApprovalCard({
            state: this.state.approvalPending,
            onChoice: (c: string) => { this.vm?.submitApproval(c) },
          })
        }
        if (this.state.streamingContent.length > 0) {
          MarkdownText({ src: this.state.streamingContent })
            .constraintSize({ maxWidth: '100%' })
            .backgroundColor(LightTheme.surface)
            .borderRadius({ topLeft: 16, topRight: 16, bottomLeft: 4, bottomRight: 16 })
            .padding({ left: 14, right: 14, top: 10, bottom: 10 })
        } else if (this.state.thinkingText.length === 0 && this.state.toolCalls.length === 0) {
          // loading 指示器（对齐 android StreamingBubble 空内容态：spinner 12 + 思考中 labelSmall）
          Row() {
            LoadingProgress()
              .width(12)
              .height(12)
              .color(LightTheme.muted)
            Text('思考中')
              .fontSize(Typography.labelSmall)
              .fontColor(LightTheme.muted)
              .margin({ left: 6 })
          }
          .alignItems(VerticalAlign.Center)
          .padding({ left: 14, right: 14, top: 12, bottom: 12 })
          .backgroundColor(LightTheme.surface)
          .borderRadius({ topLeft: 16, topRight: 16, bottomLeft: 4, bottomRight: 16 })
          .constraintSize({ maxWidth: 120 })
        }
      }
      .alignItems(HorizontalAlign.Start)
      .width('100%')
      .padding({ left: 16, right: 16, top: 6, bottom: 6 })
    }
  }

  @Builder
  modelSheet(): void {
    Column() {
      Text('选择模型')
        .fontSize(Typography.titleMedium)
        .fontWeight(FontWeight.Bold)
        .fontColor(LightTheme.text)
        .margin({ bottom: 8 })
        .width('100%')
      if (this.state.models.length === 0) {
        Text('暂无可用模型')
          .fontSize(Typography.bodySmall)
          .fontColor(LightTheme.muted)
          .width('100%')
      } else {
        ForEach(this.state.models, (m: string) => {
          Row() {
            Text(m)
              .fontSize(Typography.bodyMedium)
              .fontColor(LightTheme.text)
              .layoutWeight(1)
            if (m === this.state.currentModel) {
              MIcon({ path: IconPaths.check, iconSize: 24, color: LightTheme.accent })
            }
          }
          .width('100%')
          .padding({ left: 12, right: 12, top: 10, bottom: 10 })
          .alignItems(VerticalAlign.Center)
          .backgroundColor(m === this.state.currentModel ? LightTheme.accentContainer : LightTheme.surface)
          .borderRadius(8)
          .margin({ bottom: 4 })
          .onClick(() => { this.vm?.selectModel(m) })
        }, (m: string) => m)
      }
      Blank()
        .height(8)
    }
    .width('100%')
    .padding(16)
    .alignItems(HorizontalAlign.Start)
  }

  @Builder
  overflowMenu(): void {
    Column() {
      this.overflowMenuItem(IconPaths.code, '导出 JSON', LightTheme.text, () => { this.exportSession(true) })
      this.overflowMenuItem(IconPaths.description, '下载转录 Markdown', LightTheme.text, () => { this.exportSession(false) })
      Divider()
        .color(LightTheme.borderVariant)
        .strokeWidth(0.5)
      this.overflowMenuItem(IconPaths.delete, '清空对话', LightTheme.error, () => { this.showClearConfirm = true })
    }
    .width(220)
    .padding({ top: 4, bottom: 4 })
    .backgroundColor(LightTheme.surface)
  }

  @Builder
  overflowMenuItem(icon: string, label: string, color: number, onTap: () => void): void {
    Row() {
      MIcon({ path: icon, iconSize: 18, color: color })
      Text(label)
        .fontSize(Typography.bodyMedium)
        .fontColor(color)
        .margin({ left: 12 })
    }
    .width('100%')
    .padding({ left: 16, right: 16, top: 10, bottom: 10 })
    .alignItems(VerticalAlign.Center)
    .onClick(onTap)
  }

  @Builder
  clearConfirmDialog(): void {
    Stack() {
      Column() {}
      .width('100%')
      .height('100%')
      .backgroundColor(0x52000000)
      .onClick(() => { this.showClearConfirm = false })

      Column() {
        Text('清空当前对话？')
          .fontSize(Typography.titleMedium)
          .fontWeight(FontWeight.Bold)
          .fontColor(LightTheme.text)
          .width('100%')
        Text('将删除当前会话并新建一个空会话，历史消息不可恢复。')
          .fontSize(Typography.bodyMedium)
          .fontColor(LightTheme.muted)
          .margin({ top: 12 })
          .width('100%')
        Row() {
          Blank()
            .layoutWeight(1)
          Text('取消')
            .fontSize(Typography.labelLarge)
            .fontColor(LightTheme.accent)
            .padding({ left: 16, right: 16, top: 8, bottom: 8 })
            .onClick(() => { this.showClearConfirm = false })
          Text('清空')
            .fontSize(Typography.labelLarge)
            .fontWeight(FontWeight.Medium)
            .fontColor(LightTheme.error)
            .padding({ left: 16, right: 16, top: 8, bottom: 8 })
            .margin({ left: 8 })
            .onClick(() => {
              this.showClearConfirm = false
              this.vm?.clearConversation()
            })
        }
        .width('100%')
        .margin({ top: 24 })
        .alignItems(VerticalAlign.Center)
      }
      .width('85%')
      .backgroundColor(LightTheme.surface)
      .borderRadius(20)
      .padding(20)
      .alignItems(HorizontalAlign.Start)
      .shadow({ radius: 8, color: 0x26000000, offsetX: 0, offsetY: 2 })
    }
    .width('100%')
    .height('100%')
    .position({ x: 0, y: 0 })
  }

  build(): void {
    Column() {
      // 顶部标题栏（对齐 android ChatTopBar：左返回 + titleMedium SemiBold 标题 + 右侧操作按钮）
      Row() {
        Row() {
          MIcon({ path: IconPaths.arrowBack, iconSize: 24, color: LightTheme.text })
        }
        .width(48)
        .height(48)
        .alignItems(VerticalAlign.Center)
        .justifyContent(FlexAlign.Center)
        .onClick(() => {
          AppRouter.instance().replace(Routes.AGENT_LIST)
        })

        Text(this.state.agentName ?? '智能体')
          .fontSize(Typography.titleMedium)
          .fontWeight(FontWeight.Bold)
          .fontColor(LightTheme.text)
          .layoutWeight(1)
          .maxLines(1)
          .textOverflow({ overflow: TextOverflow.Ellipsis })

        Row() {
          MIcon({
            path: this.state.autoSpeak ? IconPaths.volumeUp : IconPaths.volumeUpOutline,
            iconSize: 24,
            color: this.state.autoSpeak ? LightTheme.accent : LightTheme.muted,
          })
        }
        .width(48)
        .height(48)
        .alignItems(VerticalAlign.Center)
        .justifyContent(FlexAlign.Center)
        .onClick(() => { this.vm?.toggleAutoSpeak() })

        Row() {
          MIcon({ path: IconPaths.menu, iconSize: 24, color: LightTheme.text })
        }
        .width(48)
        .height(48)
        .alignItems(VerticalAlign.Center)
        .justifyContent(FlexAlign.Center)
        .onClick(() => { this.openDrawerAnimated() })

        Row() {
          MIcon({ path: IconPaths.folder, iconSize: 24, color: LightTheme.text })
        }
        .width(48)
        .height(48)
        .alignItems(VerticalAlign.Center)
        .justifyContent(FlexAlign.Center)
        .onClick(() => {
          const aid = this.state.agentId
          if (aid === null || aid.length === 0) return
          const navParam: Record<string, string> = { 'agentId': aid }
          AppRouter.instance().navigate(Routes.WORKSPACE, navParam)
        })

        // 溢出菜单：仅在有会话时显示（对齐 android hasSession 分支）
        if (this.state.currentSessionId !== null) {
          Row() {
            MIcon({ path: IconPaths.moreVert, iconSize: 24, color: LightTheme.text })
          }
          .width(48)
          .height(48)
          .alignItems(VerticalAlign.Center)
          .justifyContent(FlexAlign.Center)
          .bindMenu(this.overflowMenu())
        }
      }
      .width('100%')
      .height(64)
      .padding({ left: 4, right: 4 })
      .alignItems(VerticalAlign.Center)
      .backgroundColor(LightTheme.bg)

      if (this.state.bootstrapping) {
        Column() {
          LoadingProgress()
            .width(28)
            .height(28)
            .color(LightTheme.accent)
        }
        .width('100%')
        .layoutWeight(1)
        .justifyContent(FlexAlign.Center)
      } else if (!this.state.engineAvailable) {
        Column() {
          Text('引擎暂不可用')
            .fontSize(Typography.titleMedium)
            .fontWeight(FontWeight.Bold)
            .fontColor(LightTheme.error)
            .margin({ bottom: 8 })
          Text(this.state.deployErrorMessage ?? '')
            .fontSize(Typography.bodySmall)
            .fontColor(LightTheme.muted)
            .textAlign(TextAlign.Center)
            .padding({ left: 32, right: 32 })
        }
        .width('100%')
        .layoutWeight(1)
        .justifyContent(FlexAlign.Center)
      } else if (this.state.messages.length === 0 && !this.showStreamingBubble()
        && this.state.thinkingText.length === 0 && this.state.toolCalls.length === 0
        && this.state.approvalPending === null) {
        // 空会话占位（对齐 android isEmpty 分支）
        Column() {
          Text('发送消息给智能体…')
            .fontSize(Typography.bodySmall)
            .fontColor(LightTheme.muted)
        }
        .width('100%')
        .layoutWeight(1)
        .justifyContent(FlexAlign.Center)
      } else {
        // 消息列表
        List({ scroller: this.listScroller }) {
          ForEach(this.state.messages, (msg: Message, idx: number) => {
            ListItem() {
              this.messageItem(msg, idx)
            }
            // key 含反馈/收藏/朗读位：ArkUI ForEach 同 key 复用缓存项不重渲染，
            // 这些行内状态变化必须换 key 触发该项重建，否则图标颜色定格在首帧
          }, (msg: Message, idx: number) => {
            const ref = MessageFeedbackRepository.messageRefOf(msg)
            const fb = this.state.feedback[ref] ?? ''
            const fav = this.state.favorites.has(ref) ? '1' : '0'
            const spk = this.state.speakingRef === ref ? '1' : '0'
            return `${idx}-${msg.id ?? idx}-${fb}-${fav}-${spk}`
          })

          ListItem() {
            this.streamingItem()
          }
        }
        .layoutWeight(1)
        .padding({ top: 6, bottom: 6 })
        .scrollBar(BarState.Auto)
        .onDidScroll(() => {
          // 持续采样贴底状态，供 IME 压缩时判定是否补钉
          this.wasAtBottom = this.listScroller.isAtEnd()
        })
        .onAreaChange((oldArea: Area, newArea: Area) => {
          // RESIZE 避键盘：IME 弹起列表被压缩。仅压缩前贴底才补钉；
          // 翻历史时（wasAtBottom=false）List 保持原位不打断阅读。
          const oldH = oldArea.height as number
          const newH = newArea.height as number
          if (oldH > 0 && newH < oldH - 40 && this.wasAtBottom && this.state.messages.length > 0) {
            this.listScroller.scrollEdge(Edge.Bottom)
          }
        })
      }

      if (!this.state.bootstrapping && this.state.engineAvailable) {
        // 底部输入栏
        Composer({
          isStreaming: this.state.isStreaming,
          onSend: (t: string, attachments: Attachment[]) => { this.vm?.sendMessage(t, attachments) },
          onCancel: () => { this.vm?.cancel() },
          onOpenModelSheet: () => { this.vm?.openModelSheet() },
          onTranscribe: async (audio: Uint8Array): Promise<string | null> => {
            const vm = this.vm
            if (vm === null) return null
            return await vm.transcribeAudio(audio)
          },
          onToast: (msg: string) => { this.vm?.showToast(msg) },
        })
      }

      // 会话抽屉：遮罩 + 左侧面板（对齐 android ModalNavigationDrawer）
      if (this.state.drawerOpen) {
        Stack() {
          Column() {}
          .width('100%')
          .height('100%')
          .backgroundColor(0x52000000)
          .transition(TransitionEffect.OPACITY.animation({ duration: 250 }))
          .onClick(() => { this.closeDrawerAnimated() })

          Row() {
            SessionDrawer({
              sessions: this.state.sessions,
              currentSessionId: this.state.currentSessionId ?? '',
              loading: this.state.loadingSessions,
              multiSelectMode: this.state.multiSelectMode,
              selectedSessionIds: Array.from(this.state.selectedSessionIds),
              onSelect: (sessionId: string) => {
                this.closeDrawerAnimated()
                this.vm?.switchSession(sessionId)
              },
              onNew: () => {
                this.closeDrawerAnimated()
                this.vm?.newSession()
              },
              onRename: (sessionId: string, title: string) => { this.vm?.renameSession(sessionId, title) },
              onDelete: (sessionId: string) => { this.vm?.deleteSession(sessionId) },
              onClose: () => { this.closeDrawerAnimated() },
              onEnterMultiSelect: (sessionId: string) => { this.vm?.enterMultiSelect(sessionId) },
              onToggleSelected: (sessionId: string) => { this.vm?.toggleSessionSelected(sessionId) },
              onSelectAll: () => { this.vm?.selectAllSessions() },
              onCancelMultiSelect: () => { this.vm?.cancelMultiSelect() },
              onDeleteSelected: () => { this.vm?.deleteSelectedSessions() },
            })
          }
          .width('85%')
          .constraintSize({ maxWidth: 360 })
          .height('100%')
          .backgroundColor(LightTheme.surface)
          .transition(TransitionEffect.move(TransitionEdge.START).animation({ duration: 250 }))
        }
        .width('100%')
        .height('100%')
        .position({ x: 0, y: 0 })
        .alignContent(Alignment.TopStart)
      }

      // 清空对话确认弹窗
      if (this.showClearConfirm) {
        this.clearConfirmDialog()
      }

      // 全屏图片预览（点击附件缩略图打开，点击任意处关闭）
      if (this.viewerOpen) {
        Stack() {
          if (this.viewerUri !== null) {
            Image(this.viewerUri)
              .width('100%')
              .height('100%')
              .objectFit(ImageFit.Contain)
          } else if (this.viewerPm !== null) {
            Image(this.viewerPm)
              .width('100%')
              .height('100%')
              .objectFit(ImageFit.Contain)
          }
        }
        .width('100%')
        .height('100%')
        .backgroundColor(0xCC000000)
        .position({ x: 0, y: 0 })
        .onClick(() => {
          this.viewerOpen = false
          this.viewerUri = null
          this.viewerPm = null
        })
      }
    }
    .width('100%')
    .height('100%')
    .backgroundColor(LightTheme.bg)
    .bindSheet(this.state.modelSheetOpen, this.modelSheet(), {
      detents: [SheetSize.FIT_CONTENT],
      backgroundColor: LightTheme.surface,
      onDisappear: () => { this.vm?.closeModelSheet() },
    })
  }
}
