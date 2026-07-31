import { MineViewModel } from './MineViewModel';
import { MineUiState } from './MineUiState';
import { AppContainer } from '../../di/AppContainer';
import { TopBar, SettingsCard } from './SettingsCommons';
import { AppRouter, Routes } from '../nav/Routes';
import { LightTheme } from '../theme/Colors';
import { Typography } from '../theme/Typography';
import { IconPaths, MIcon } from '../theme/Icons';

/**
 * 账号设置页。复刻 Android ui/mine/AccountSettingsScreen.kt。
 * 头像 / 真实姓名 / 邮箱 / 手机 各自点进去详情页编辑；邮箱/手机未认证时显示「认证」按钮。
 */

@Component
export struct AccountSettingsScreen {
  @Consume('container') container: AppContainer
  @State state: MineUiState = new MineUiState()
  private vm: MineViewModel | null = null
  private listener: (s: MineUiState) => void = (s) => {
    this.state = s
    if (s.profileError !== null) {
      this.vm?.clearProfileError()
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
  }

  aboutToDisappear(): void {
    if (this.vm !== null) this.vm.unsubscribe(this.listener)
  }

  onBack(): void {
    AppRouter.instance().pop()
  }

  onEditAvatar(): void {
    AppRouter.instance().navigate(Routes.EDIT_AVATAR)
  }

  onEditField(field: string): void {
    const navParam: Record<string, string> = { 'field': field }
    AppRouter.instance().navigate(Routes.EDIT_TEXT, navParam)
  }

  onVerifyContact(channel: string): void {
    const navParam: Record<string, string> = { 'channel': channel }
    AppRouter.instance().navigate(Routes.VERIFY_CONTACT, navParam)
  }

  private email(): string | null {
    const e = this.state.user?.email
    if (e === null || e === undefined || e.length === 0) return null
    return e
  }

  private phone(): string | null {
    const p = this.state.user?.phone
    if (p === null || p === undefined || p.length === 0) return null
    return p
  }

  @Builder
  avatarRow(): void {
    Row() {
      Text('头像')
        .fontSize(Typography.bodyLarge)
        .fontColor(LightTheme.text)
        .layoutWeight(1)
      if (this.state.user !== null && this.state.user.avatar_url !== null && this.state.user.avatar_url.length > 0) {
        Image(avatarAbsoluteUrl(this.state.user.avatar_url, this.container))
          .width(36)
          .height(36)
          .borderRadius(18)
          .objectFit(ImageFit.Cover)
      } else {
        Circle({ width: 36, height: 36 })
          .fill(LightTheme.accent)
      }
      Text('点击更换')
        .fontSize(Typography.bodySmall)
        .fontColor(LightTheme.muted)
        .margin({ left: 8, right: 4 })
      MIcon({ path: IconPaths.keyboardArrowRight, iconSize: 20, color: LightTheme.muted })
    }
    .width('100%')
    .constraintSize({ minHeight: 64 })
    .padding({ left: 20, right: 20, top: 14, bottom: 14 })
    .alignItems(VerticalAlign.Center)
    .onClick(() => { this.onEditAvatar() })

    Divider()
      .color(LightTheme.border)
      .strokeWidth(0.5)
      .margin({ left: 20 })
  }

  @Builder
  realNameRow(): void {
    Row() {
      MIcon({ path: IconPaths.badge, iconSize: 22, color: LightTheme.muted })
        .margin({ right: 14 })
      Text('真实姓名')
        .fontSize(Typography.bodyLarge)
        .fontColor(LightTheme.text)
        .layoutWeight(1)
      Text(this.state.user?.real_name ?? '未设置')
        .fontSize(Typography.bodyMedium)
        .fontColor(LightTheme.muted)
        .margin({ right: 8 })
      MIcon({ path: IconPaths.keyboardArrowRight, iconSize: 20, color: LightTheme.muted })
    }
    .width('100%')
    .constraintSize({ minHeight: 64 })
    .padding({ left: 20, right: 20, top: 14, bottom: 14 })
    .alignItems(VerticalAlign.Center)
    .onClick(() => { this.onEditField('real_name') })
  }

  private contactValue(channel: string): string | null {
    return channel === 'email' ? this.email() : this.phone()
  }

  private contactVerified(channel: string): boolean {
    return channel === 'email'
      ? (this.state.user?.email_verified ?? false)
      : (this.state.user?.phone_verified ?? false)
  }

  private contactCanVerify(channel: string): boolean {
    const v = this.contactValue(channel)
    const verified = this.contactVerified(channel)
    return channel === 'email'
      ? (this.state.emailChannelEnabled && v !== null && !verified)
      : (this.state.smsChannelEnabled && v !== null && !verified)
  }

  // channel 是常量（'email'/'phone'），值/认证态在 builder 内经 helper 直读 state——
  // @Builder 值参数快照不随 @State 刷新（见 MineScreen avatarBlock 修复）
  @Builder
  contactRow(channel: string): void {
    Row() {
      MIcon({ path: channel === 'email' ? IconPaths.mail : IconPaths.phoneIphone, iconSize: 22, color: LightTheme.muted })
        .margin({ right: 14 })
      Column() {
        Text(channel === 'email' ? '邮箱' : '手机')
          .fontSize(Typography.bodyLarge)
          .fontColor(LightTheme.text)
        Text(this.contactValue(channel) ?? '未设置')
          .fontSize(Typography.bodyMedium)
          .fontColor(LightTheme.muted)
          .margin({ top: 2 })
      }
      .alignItems(HorizontalAlign.Start)
      .layoutWeight(1)

      // verification pill
      this.verificationPill(channel)
    }
    .width('100%')
    .constraintSize({ minHeight: 64 })
    .padding({ left: 20, right: 20, top: 14, bottom: 14 })
    .alignItems(VerticalAlign.Center)
    .onClick(() => { this.onEditField(channel) })
  }

  @Builder
  verificationPill(channel: string): void {
    if (this.contactValue(channel) === null) {
      Text('未设置')
        .fontSize(Typography.bodySmall)
        .fontColor(LightTheme.muted)
    } else if (this.contactVerified(channel)) {
      Row() {
        MIcon({ path: IconPaths.checkCircle, iconSize: 14, color: LightTheme.accent })
        Text('已认证')
          .fontSize(Typography.bodySmall)
          .fontColor(LightTheme.accent)
          .margin({ left: 4 })
      }
      .alignItems(VerticalAlign.Center)
    } else if (this.contactCanVerify(channel)) {
      Row() {
        Text('认证')
          .fontSize(Typography.labelMedium)
          .fontColor(LightTheme.accent)
      }
      .padding({ left: 10, right: 10, top: 4, bottom: 4 })
      .borderRadius(50)
      .backgroundColor(LightTheme.surfaceVariant)
      .onClick(() => { this.onVerifyContact(channel) })
    } else {
      Text('未认证')
        .fontSize(Typography.bodySmall)
        .fontColor(LightTheme.muted)
    }
  }

  @Builder
  avatarCard(): void {
    this.avatarRow()
    this.realNameRow()
  }

  @Builder
  emailCard(): void {
    this.contactRow('email')
  }

  @Builder
  phoneCard(): void {
    this.contactRow('phone')
  }

  build(): void {
    Column() {
      TopBar('账号设置', () => this.onBack())
      Scroll() {
        Column() {
          SettingsCard({ content: (): void => this.avatarCard() })

          Blank().height(12)

          SettingsCard({ content: (): void => this.emailCard() })

          Blank().height(12)

          SettingsCard({ content: (): void => this.phoneCard() })

          Text('修改邮箱或手机后，认证状态会重置，需重新认证。')
            .fontSize(Typography.bodySmall)
            .fontColor(LightTheme.muted)
            .margin({ top: 8, left: 8, right: 8 })
            .width('100%')
        }
        .width('100%')
        .padding({ left: 16, right: 16, top: 8, bottom: 16 })
        .alignItems(HorizontalAlign.Start)
        // Scroll 内容不足一屏时默认居中；撑满高度强制顶部对齐
        .constraintSize({ minHeight: '100%' })
      }
      .layoutWeight(1)
      .scrollBar(BarState.Off)
    }
    .width('100%')
    .height('100%')
    .backgroundColor(LightTheme.bg)
  }
}

function avatarAbsoluteUrl(avatarUrl: string, container: AppContainer): string {
  if (avatarUrl.startsWith('http://') || avatarUrl.startsWith('https://')) return avatarUrl
  const base = container.serverConfig.managerUrl.replace(/\/$/, '').replace(/api\/manager\/?$/, '').replace(/\/$/, '')
  const path = avatarUrl.startsWith('/') ? avatarUrl : `/${avatarUrl}`
  return base + path
}
