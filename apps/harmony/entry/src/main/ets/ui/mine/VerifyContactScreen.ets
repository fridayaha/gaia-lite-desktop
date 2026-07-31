import { MineViewModel } from './MineViewModel';
import { MineUiState } from './MineUiState';
import { AppContainer } from '../../di/AppContainer';
import { TopBar } from './SettingsCommons';
import { AppRouter } from '../nav/Routes';
import { LightTheme } from '../theme/Colors';
import { Typography } from '../theme/Typography';
import { IconPaths, MIcon } from '../theme/Icons';
import { CaptchaResponse } from '../../net/dto/AuthDtos';

/**
 * 邮箱/手机认证页。复刻 Android ui/mine/VerifyContactScreen.kt。
 */

@Component
export struct VerifyContactScreen {
  @Consume('container') container: AppContainer
  @Prop channel: string = ''
  @State state: MineUiState = new MineUiState()
  @State captcha: CaptchaResponse | null = null
  @State captchaAnswer: string = ''
  @State sendingCode: boolean = false
  @State codeSent: boolean = false
  @State code: string = ''
  @State error: string | null = null
  @State pendingVerify: boolean = false
  private vm: MineViewModel | null = null
  private listener: (s: MineUiState) => void = (s) => {
    this.state = s
    if (s.profileError !== null && (this.sendingCode || this.pendingVerify)) {
      this.error = s.profileError
      this.sendingCode = false
      this.pendingVerify = false
      this.vm?.clearProfileError()
    }
    if (this.pendingVerify && !s.contactVerifying && s.profileError === null) {
      this.pendingVerify = false
      this.onBack()
    } else if (this.pendingVerify && !s.contactVerifying && s.profileError !== null) {
      this.pendingVerify = false
    }
  }

  aboutToAppear(): void {
    this.vm = new MineViewModel(
      this.container.authRepository,
      this.container.agentContext,
      this.container.appReleaseRepository,
      this.container.messageFeedbackRepository,
      this.container.developerModeStore,
      this.container.accountStore,
      this.container.hapDownloader,
      this.container.serverConfig,
      this.container.tokenStorage,
    )
    this.vm.subscribe(this.listener)
    this.vm.bootstrap()
    this.loadCaptcha()
  }

  aboutToDisappear(): void {
    if (this.vm !== null) this.vm.unsubscribe(this.listener)
  }

  private title(): string {
    if (this.channel === 'email') return '邮箱认证'
    if (this.channel === 'phone') return '手机认证'
    return '认证'
  }

  private target(): string {
    const user = this.state.user
    if (user === null) return ''
    if (this.channel === 'email') return user.email ?? ''
    if (this.channel === 'phone') return user.phone ?? ''
    return ''
  }

  private async loadCaptcha(): Promise<void> {
    try {
      const cap = await this.container.authRepository.getCaptcha()
      this.captcha = cap
    } catch (e) {
      this.error = '图形验证码加载失败'
    }
  }

  onBack(): void {
    AppRouter.instance().pop()
  }

  async onSendCode(): Promise<void> {
    if (this.captcha === null) {
      this.error = '验证码未加载'
      return
    }
    if (this.captchaAnswer.trim().length === 0) {
      this.error = '请输入图形验证码'
      return
    }
    const target = this.target()
    if (target.length === 0) {
      this.error = '未设置联系方式'
      return
    }
    this.sendingCode = true
    this.error = null
    try {
      const purpose = this.channel === 'email' ? 'verify_email' : 'verify_phone'
      await this.container.authRepository.sendVerificationCode(
        this.channel, target, purpose, this.captcha.captcha_id, this.captchaAnswer.trim(),
      )
      this.codeSent = true
      this.error = null
    } catch (e) {
      this.error = e instanceof Error ? (e.message || '发送验证码失败') : '发送验证码失败'
      try { await this.loadCaptcha() } catch (_) {}
    } finally {
      this.sendingCode = false
    }
  }

  async onVerify(): Promise<void> {
    if (this.code.length !== 6) {
      this.error = '请输入 6 位验证码'
      return
    }
    this.pendingVerify = true
    this.error = null
    if (this.channel === 'email') {
      await this.vm?.verifyEmail(this.code)
    } else if (this.channel === 'phone') {
      await this.vm?.verifyPhone(this.code)
    }
  }

  async onResend(): Promise<void> {
    await this.loadCaptcha()
    this.codeSent = false
    this.code = ''
    this.error = null
  }

  build(): void {
    Column() {
      TopBar(this.title(), () => this.onBack())
      Scroll() {
        Column() {
          Text(`向 ${this.target()} 发送验证码`)
            .fontSize(Typography.bodyMedium)
            .fontColor(LightTheme.text)
            .width('100%')
            .margin({ bottom: 12 })

          if (!this.codeSent) {
            if (this.captcha !== null) {
              Row() {
                Image(captchaDataUri(this.captcha))
                  .width(160)
                  .height(60)
                  .objectFit(ImageFit.Cover)
                  .borderRadius(8)
                  .onClick(() => { this.loadCaptcha() })
              }
              .width('100%')
              .margin({ bottom: 4 })
              Text('点击图片刷新')
                .fontSize(Typography.labelSmall)
                .fontColor(LightTheme.muted)
                .margin({ bottom: 12 })
            } else {
              Text('图形验证码加载中…')
                .fontSize(Typography.labelSmall)
                .fontColor(LightTheme.muted)
                .margin({ bottom: 12 })
            }

            TextInput({ placeholder: '图形验证码', text: this.captchaAnswer })
              .width('100%')
              .height(48)
              .onChange((v) => { this.captchaAnswer = v; this.error = null })

            if (this.error !== null) {
              Text(this.error ?? '')
                .fontSize(Typography.labelSmall)
                .fontColor(LightTheme.error)
                .margin({ top: 8 })
                .width('100%')
            }

            Button(this.sendingCode ? '发送中…' : '发送验证码')
              .type(ButtonType.Capsule)
              .width('100%')
              .margin({ top: 12 })
              .enabled(!this.sendingCode && this.captchaAnswer.trim().length > 0 && this.captcha !== null)
              .onClick(() => { this.onSendCode() })
          } else {
            Text('验证码已发送，请输入收到的 6 位数字')
              .fontSize(Typography.bodyMedium)
              .fontColor(LightTheme.text)
              .margin({ bottom: 12 })
              .width('100%')

            TextInput({ placeholder: '验证码', text: this.code })
              .width('100%')
              .height(48)
              .type(InputType.Number)
              .maxLength(6)
              .onChange((v) => {
                const digits = v.replace(/[^0-9]/g, '').slice(0, 6)
                this.code = digits
                this.error = null
              })

            if (this.error !== null) {
              Text(this.error ?? '')
                .fontSize(Typography.labelSmall)
                .fontColor(LightTheme.error)
                .margin({ top: 8 })
                .width('100%')
            }

            Button(this.state.contactVerifying ? '认证中…' : '提交认证')
              .type(ButtonType.Capsule)
              .width('100%')
              .margin({ top: 12 })
              .enabled(!this.state.contactVerifying && this.code.length === 6)
              .onClick(() => { this.onVerify() })

            Row() {
              MIcon({ path: IconPaths.refresh, iconSize: 16, color: LightTheme.accent })
              Text('重新发送')
                .fontSize(Typography.labelMedium)
                .fontColor(LightTheme.accent)
                .margin({ left: 4 })
            }
            .margin({ top: 12 })
            .alignItems(VerticalAlign.Center)
            .onClick(() => { this.onResend() })
          }
        }
        .width('100%')
        .padding(16)
        .alignItems(HorizontalAlign.Start)
        // Scroll 内容不足一屏时默认居中；撑满高度强制顶部对齐
        .constraintSize({ minHeight: '100%' })
      }
      .layoutWeight(1)
      .scrollBar(BarState.Auto)
    }
    .width('100%')
    .height('100%')
    .backgroundColor(LightTheme.bg)
  }
}

function captchaDataUri(cap: CaptchaResponse): string {
  if (cap.image_base64.startsWith('data:')) return cap.image_base64
  return `data:image/png;base64,${cap.image_base64}`
}
