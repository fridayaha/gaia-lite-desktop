import fs from '@ohos.file.fs';
import common from '@ohos.app.ability.common';
import abilityAccessCtrl, { Permissions } from '@ohos.abilityAccessCtrl';
import bundleManager from '@ohos.bundle.bundleManager';
import { picker } from '@kit.CoreFileKit';
import cameraPicker from '@ohos.multimedia.cameraPicker';
import camera from '@ohos.multimedia.camera';
import { LightTheme } from '../theme/Colors';
import { Typography } from '../theme/Typography';
import { IconPaths, MIcon } from '../theme/Icons';
import { Attachment } from '../../net/dto/ChatDtos';
import { AudioRecorder } from '../../repo/AudioRecorder';
import { readUriBytes } from '../../repo/ChatRepository';
import { guessMime, fileNameFromUri } from '../../util/Mime';
import { logI } from '../../util/Logger';

const TAG = 'Composer'
const MIC_PERMISSION: Permissions = 'ohos.permission.MICROPHONE'

/**
 * 输入栏（Composer）。复刻 Android ui/chat/Composer.kt：
 * - 附件托盘：待发送附件横排（图片缩略图/文件图标+名+移除），发送时随消息一起走
 * - 附件面板：拍照(CameraPicker)/相册(PhotoViewPicker)/文件(DocumentViewPicker)/录音(AVRecorder m4a)
 * - 语音输入：mic 切换 voiceMode → 「按住 说话」条，按下录音松开识别回填并自动发送
 * - 附件录音：红点计时 + 停止按钮，停止后入托盘
 */
@Component
export struct Composer {
  @Prop isStreaming: boolean = false
  @State text: string = ''
  @State attachPanelOpen: boolean = false
  @State pending: Attachment[] = []
  @State voiceMode: boolean = false
  @State isRecording: boolean = false
  @State recordingSeconds: number = 0
  @State holdRecording: boolean = false
  @State holdSeconds: number = 0
  @State transcribing: boolean = false
  onSend: (text: string, attachments: Attachment[]) => void = () => {}
  onCancel: () => void = () => {}
  onOpenModelSheet: () => void = () => {}
  onTranscribe: (audio: Uint8Array) => Promise<string | null> = async () => null
  onToast: (msg: string) => void = () => {}

  private attachRecorder: AudioRecorder | null = null
  private holdRecorder: AudioRecorder | null = null
  private recTimer: number = -1
  private holdTimer: number = -1
  private holdStartMs: number = 0
  /** 手势按下标记：recorder 异步启动完成前松手时，由启动完成分支收尾 */
  private holdActive: boolean = false

  aboutToDisappear(): void {
    this.stopTimer()
    this.stopHoldTimer()
    const a = this.attachRecorder
    this.attachRecorder = null
    if (a !== null) a.discard()
    const h = this.holdRecorder
    this.holdRecorder = null
    if (h !== null) h.discard()
  }

  doSend(): void {
    const t = this.text.trim()
    if (t.length === 0 && this.pending.length === 0) return
    if (this.isStreaming) return
    this.onSend(t, this.pending)
    this.text = ''
    this.pending = []
  }

  // ── 附件选取 ──

  private addPicked(uri: string): void {
    const name = fileNameFromUri(uri)
    const mime = guessMime(name)
    const a = new Attachment()
    a.name = name.length > 0 ? name : `upload-${Date.now()}`
    a.mime = mime
    a.localUri = uri
    a.is_image = mime.startsWith('image/')
    this.pending = [...this.pending, a]
  }

  private async pickFromGallery(): Promise<void> {
    try {
      const opts = new picker.PhotoSelectOptions()
      opts.MIMEType = picker.PhotoViewMIMETypes.IMAGE_TYPE
      opts.maxSelectNumber = 9
      const photoPicker = new picker.PhotoViewPicker(getContext(this))
      const result = await photoPicker.select(opts)
      const uris = result.photoUris ?? []
      for (const uri of uris) {
        this.addPicked(uri)
      }
    } catch (e) {
      logI(TAG, `gallery pick cancelled/failed: ${e instanceof Error ? e.message : String(e)}`)
    }
  }

  private async pickDocument(): Promise<void> {
    try {
      const opts = new picker.DocumentSelectOptions()
      opts.maxSelectNumber = 9
      const docPicker = new picker.DocumentViewPicker(getContext(this))
      const uris = await docPicker.select(opts)
      for (const uri of uris) {
        this.addPicked(uri)
      }
    } catch (e) {
      logI(TAG, `document pick cancelled/failed: ${e instanceof Error ? e.message : String(e)}`)
    }
  }

  private async takePhoto(): Promise<void> {
    try {
      const ctx = getContext(this) as common.UIAbilityContext
      const profile: cameraPicker.PickerProfile = {
        cameraPosition: camera.CameraPosition.CAMERA_POSITION_BACK,
      }
      const result = await cameraPicker.pick(ctx, [cameraPicker.PickerMediaType.PHOTO], profile)
      if (result.resultCode === 0 && result.resultUri.length > 0) {
        this.addPicked(result.resultUri)
      }
    } catch (e) {
      logI(TAG, `camera pick cancelled/failed: ${e instanceof Error ? e.message : String(e)}`)
    }
  }

  // ── 麦克风权限 ──

  private async ensureMicPermission(): Promise<boolean> {
    try {
      const atManager = abilityAccessCtrl.createAtManager()
      const info = bundleManager.getBundleInfoForSelfSync(
        bundleManager.BundleFlag.GET_BUNDLE_INFO_WITH_APPLICATION
      )
      const tokenId = info.appInfo.accessTokenId
      const status = atManager.checkAccessTokenSync(tokenId, MIC_PERMISSION)
      if (status === abilityAccessCtrl.GrantStatus.PERMISSION_GRANTED) return true
      const ctx = getContext(this) as common.UIAbilityContext
      const result = await atManager.requestPermissionsFromUser(ctx, [MIC_PERMISSION])
      const r = result.authResults
      return r.length > 0 && r[0] === 0
    } catch (e) {
      logI(TAG, `mic permission check failed: ${e instanceof Error ? e.message : String(e)}`)
      return false
    }
  }

  // ── 附件录音（面板「录音」按钮）──

  private async startAttachRecording(): Promise<void> {
    const granted = await this.ensureMicPermission()
    if (!granted) {
      this.onToast('需要录音权限才能发送语音')
      return
    }
    const ctx = getContext(this) as common.UIAbilityContext
    const r = new AudioRecorder()
    try {
      await r.start(ctx.cacheDir)
    } catch (e) {
      this.onToast('启动录音失败')
      return
    }
    this.attachRecorder = r
    this.recordingSeconds = 0
    this.isRecording = true
    this.recTimer = setInterval(() => { this.recordingSeconds++ }, 1000)
  }

  private async stopAttachRecording(): Promise<void> {
    this.stopTimer()
    const r = this.attachRecorder
    this.attachRecorder = null
    this.isRecording = false
    this.recordingSeconds = 0
    if (r === null) return
    const path = await r.stop()
    if (path === null) {
      this.onToast('保存录音失败')
      return
    }
    this.addPicked(path)
  }

  private stopTimer(): void {
    if (this.recTimer >= 0) {
      clearInterval(this.recTimer)
      this.recTimer = -1
    }
  }

  // ── 按住说话（语音输入 → ASR → 自动发送）──

  private onHoldStart(): void {
    if (this.holdActive || this.transcribing || this.isStreaming) return
    this.holdActive = true
    this.holdStartMs = Date.now()
    this.holdSeconds = 0
    this.holdRecording = true
    this.holdTimer = setInterval(() => { this.holdSeconds++ }, 1000)
    this.startHoldRecorder()
  }

  private async startHoldRecorder(): Promise<void> {
    const granted = await this.ensureMicPermission()
    if (!granted) {
      this.cancelHold()
      this.onToast('需要录音权限才能语音输入')
      return
    }
    const ctx = getContext(this) as common.UIAbilityContext
    const r = new AudioRecorder()
    try {
      await r.start(ctx.cacheDir)
    } catch (e) {
      this.cancelHold()
      this.onToast('启动录音失败')
      return
    }
    this.holdRecorder = r
    // 异步启动完成前用户已松手：立即按正常释放流程收尾
    if (!this.holdActive) {
      await this.finishHold(true)
    }
  }

  private onHoldEnd(): void {
    if (!this.holdActive) return
    this.holdActive = false
    this.stopHoldTimer()
    if (this.holdRecorder !== null) {
      this.finishHold(true)
    }
    // recorder 未就绪：startHoldRecorder 完成后自行收尾
  }

  private onHoldCancel(): void {
    if (!this.holdActive) return
    this.holdActive = false
    this.stopHoldTimer()
    if (this.holdRecorder !== null) {
      this.finishHold(false)
    }
  }

  /** 授权失败/启动失败复位（无录音可收尾）。 */
  private cancelHold(): void {
    this.holdActive = false
    this.holdRecording = false
    this.holdSeconds = 0
    this.stopHoldTimer()
  }

  private stopHoldTimer(): void {
    if (this.holdTimer >= 0) {
      clearInterval(this.holdTimer)
      this.holdTimer = -1
    }
  }

  private async finishHold(transcribe: boolean): Promise<void> {
    const r = this.holdRecorder
    this.holdRecorder = null
    this.holdRecording = false
    this.holdSeconds = 0
    if (r === null) return
    const durationMs = Date.now() - this.holdStartMs
    const path = await r.stop()
    if (!transcribe) {
      if (path !== null) {
        try { fs.unlink(path) } catch (e) {}
      }
      return
    }
    if (path === null || durationMs < 500) {
      if (path !== null) {
        try { fs.unlink(path) } catch (e) {}
      }
      this.onToast('说话时间太短')
      return
    }
    this.transcribing = true
    try {
      const bytes = readUriBytes(path)
      const result = await this.onTranscribe(bytes)
      // 识别成功直接发送（对齐豆包语音输入）；保持语音模式便于连续对话
      if (result !== null && result.length > 0) {
        this.text = result
        this.doSend()
      }
    } catch (e) {
      this.onToast('语音识别失败，请重试')
    } finally {
      this.transcribing = false
      try { fs.unlink(path) } catch (e) {}
    }
  }

  // ── UI ──

  @Builder
  attachButton(label: string, icon: string, color: number, onTap: () => void): void {
    Column() {
      Stack() {
        Circle({ width: 48, height: 48 })
          .fill(color)
        MIcon({ path: icon, iconSize: 22, color: 0xFFFFFFFF })
      }
      .width(48)
      .height(48)
      Text(label)
        .fontSize(Typography.labelSmall)
        .fontColor(LightTheme.text)
        .margin({ top: 4 })
    }
    .alignItems(HorizontalAlign.Center)
    .onClick(onTap)
  }

  @Builder
  attachmentTray(): void {
    Row() {
      List({ space: 6 }) {
        ForEach(this.pending, (a: Attachment, idx: number) => {
          ListItem() {
            Row() {
              if (a.is_image && a.localUri !== null) {
                Image(a.localUri)
                  .width(32)
                  .height(32)
                  .borderRadius(6)
                  .objectFit(ImageFit.Cover)
              } else {
                MIcon({
                  path: (a.mime !== null && a.mime.startsWith('audio/')) ? IconPaths.mic : IconPaths.attachFile,
                  iconSize: 24,
                  color: LightTheme.accent,
                })
                  .width(32)
                  .height(32)
              }
              Text(a.name)
                .fontSize(Typography.labelSmall)
                .fontColor(LightTheme.text)
                .maxLines(1)
                .textOverflow({ overflow: TextOverflow.Ellipsis })
                .constraintSize({ maxWidth: 160 })
                .margin({ left: 6 })
              Row() {
                MIcon({ path: IconPaths.close, iconSize: 12, color: LightTheme.muted })
              }
              .width(20)
              .height(20)
              .alignItems(VerticalAlign.Center)
              .justifyContent(FlexAlign.Center)
              .margin({ left: 6 })
              .onClick(() => {
                this.pending = this.pending.filter((_: Attachment, i: number) => i !== idx)
              })
            }
            .padding({ left: 6, right: 6, top: 4, bottom: 4 })
            .backgroundColor(LightTheme.surfaceVariant)
            .borderRadius(8)
            .alignItems(VerticalAlign.Center)
          }
        }, (a: Attachment, idx: number) => `${idx}-${a.localUri ?? a.name}`)
      }
      .listDirection(Axis.Horizontal)
      .scrollBar(BarState.Off)
      .width('100%')
      .height(48)
    }
    .width('100%')
    .padding({ left: 12, right: 12, top: 4, bottom: 4 })
  }

  @Builder
  recordingBar(): void {
    Row() {
      Row() {
        Circle({ width: 10, height: 10 })
          .fill(LightTheme.error)
        Text(`录音中 ${formatDuration(this.recordingSeconds)}`)
          .fontSize(Typography.bodyMedium)
          .fontColor(LightTheme.text)
          .margin({ left: 8 })
      }
      .alignItems(VerticalAlign.Center)
      Row() {
        MIcon({ path: IconPaths.stop, iconSize: 22, color: LightTheme.error })
      }
      .width(36)
      .height(36)
      .alignItems(VerticalAlign.Center)
      .justifyContent(FlexAlign.Center)
      .onClick(() => { this.stopAttachRecording() })
    }
    .width('100%')
    .justifyContent(FlexAlign.SpaceBetween)
    .alignItems(VerticalAlign.Center)
    .padding({ left: 16, right: 16, top: 8, bottom: 8 })
    .backgroundColor(LightTheme.surfaceVariant)
    .borderRadius(28)
    .margin({ left: 12, right: 12, top: 4, bottom: 4 })
  }

  @Builder
  holdToTalkBar(): void {
    Row() {
      Text(
        this.transcribing ? '识别中…'
          : this.holdRecording ? `松开识别 · ${formatDuration(this.holdSeconds)}`
          : '按住 说话'
      )
        .fontSize(Typography.bodyMedium)
        .fontColor(this.holdRecording ? LightTheme.onAccent : LightTheme.text)
    }
    .width('100%')
    .height(40)
    .justifyContent(FlexAlign.Center)
    .alignItems(VerticalAlign.Center)
    .backgroundColor(this.holdRecording ? LightTheme.accent : LightTheme.surface)
    .borderRadius(24)
    .enabled(!this.transcribing && !this.isStreaming)
    .onTouch((e: TouchEvent) => {
      if (e.type === TouchType.Down) {
        this.onHoldStart()
      } else if (e.type === TouchType.Up) {
        this.onHoldEnd()
      } else if (e.type === TouchType.Cancel) {
        this.onHoldCancel()
      }
    })
  }

  build(): void {
    Column() {
      // 附件托盘
      if (this.pending.length > 0) {
        this.attachmentTray()
      }
      // 附件录音条
      if (this.isRecording) {
        this.recordingBar()
      }
      // 附件面板：输入框正上方，宽度对齐，间距 8
      if (this.attachPanelOpen) {
        Row() {
          this.attachButton('拍照', IconPaths.photoCamera, 0xFF3B82F6, () => {
            this.attachPanelOpen = false
            this.takePhoto()
          })
          this.attachButton('相册', IconPaths.image, 0xFF22C55E, () => {
            this.attachPanelOpen = false
            this.pickFromGallery()
          })
          this.attachButton('文件', IconPaths.attachFile, 0xFFF59E0B, () => {
            this.attachPanelOpen = false
            this.pickDocument()
          })
          this.attachButton('录音', IconPaths.mic, 0xFF8B5CF6, () => {
            this.attachPanelOpen = false
            this.startAttachRecording()
          })
        }
        .width('100%')
        .padding({ left: 12, right: 12, top: 14, bottom: 14 })
        .justifyContent(FlexAlign.SpaceEvenly)
        .backgroundColor(LightTheme.surface)
        .borderRadius(16)
        .shadow({ radius: 8, color: 0x1A000000, offsetX: 0, offsetY: 2 })
        .margin({ left: 12, right: 12, bottom: 8 })
      }

      // 输入胶囊
      Row() {
        // 左侧：流式=停止 / 空闲=模型选择
        if (this.isStreaming) {
          Row() {
            MIcon({ path: IconPaths.stop, iconSize: 22, color: LightTheme.error })
          }
          .width(48)
          .height(48)
          .alignItems(VerticalAlign.Center)
          .justifyContent(FlexAlign.Center)
          .onClick(() => { this.onCancel() })
        } else {
          Row() {
            MIcon({ path: IconPaths.tune, iconSize: 22, color: LightTheme.muted })
          }
          .width(48)
          .height(48)
          .alignItems(VerticalAlign.Center)
          .justifyContent(FlexAlign.Center)
          .onClick(() => { this.onOpenModelSheet() })
        }

        // 中部：voiceMode=按住说话条 / 否则文本输入
        if (this.voiceMode) {
          Row() {
            this.holdToTalkBar()
          }
          .layoutWeight(1)
          .margin({ left: 4, right: 4 })
        } else {
          TextInput({
            placeholder: this.isStreaming ? '等待回复…' : '发送消息给智能体…',
            text: this.text,
          })
            .layoutWeight(1)
            // 显式浅色底：透明底在部分机型上被渲染成黑色（主题默认填充），与胶囊同色即视觉透明
            .backgroundColor(LightTheme.surfaceVariant)
            .fontSize(Typography.bodyMedium)
            .fontColor(LightTheme.text)
            .placeholderColor(LightTheme.muted)
            .enterKeyType(EnterKeyType.Send)
            .enabled(!this.isStreaming && !this.isRecording && !this.holdRecording)
            .margin({ left: 4, right: 4 })
            .onChange((v) => { this.text = v })
            .onSubmit(() => { this.doSend() })
        }

        // 语音/键盘切换
        Row() {
          MIcon({
            path: this.voiceMode ? IconPaths.keyboard : IconPaths.mic,
            iconSize: 24,
            color: this.voiceMode ? LightTheme.accent : LightTheme.muted,
          })
        }
        .width(48)
        .height(48)
        .alignItems(VerticalAlign.Center)
        .justifyContent(FlexAlign.Center)
        .enabled(!this.isStreaming && !this.isRecording && !this.holdRecording && !this.transcribing)
        .onClick(() => { this.voiceMode = !this.voiceMode })

        // + 附件面板开关
        Row() {
          MIcon({
            path: IconPaths.add,
            iconSize: 24,
            color: this.attachPanelOpen ? LightTheme.accent : LightTheme.muted,
          })
        }
        .width(48)
        .height(48)
        .alignItems(VerticalAlign.Center)
        .justifyContent(FlexAlign.Center)
        .onClick(() => {
          if (!this.isStreaming && !this.isRecording && !this.holdRecording) {
            this.attachPanelOpen = !this.attachPanelOpen
          }
        })
      }
      .width('100%')
      .height(56)
      .padding({ left: 4, right: 4 })
      .alignItems(VerticalAlign.Center)
      .backgroundColor(LightTheme.surfaceVariant)
      .borderRadius(28)
      .margin({ left: 12, right: 12, top: 8, bottom: 8 })
    }
    .width('100%')
    .backgroundColor(LightTheme.bg)
  }
}

function formatDuration(seconds: number): string {
  const m = Math.floor(seconds / 60)
  const s = seconds % 60
  return `${m}:${s.toString().padStart(2, '0')}`
}
