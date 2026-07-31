import { MineViewModel } from './MineViewModel';
import { MineUiState } from './MineUiState';
import { AppContainer } from '../../di/AppContainer';
import { TopBar, SettingsCard } from './SettingsCommons';
import { AppRouter, Routes } from '../nav/Routes';
import { LightTheme } from '../theme/Colors';
import { Typography } from '../theme/Typography';
import { IconPaths, MIcon } from '../theme/Icons';

/**
 * 编辑个人资料入口页。复刻 Android ui/mine/ProfileEditScreen.kt。
 * 头像 + 真实姓名两行，点进去各自开详情页编辑。
 */

@Component
export struct ProfileEditScreen {
  @Consume('container') container: AppContainer
  @State state: MineUiState = new MineUiState()
  private vm: MineViewModel | null = null
  private listener: (s: MineUiState) => void = (s) => { this.state = s }

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

  @Builder
  profileCard(): void {
    this.avatarRow()
    this.realNameRow()
  }

  build(): void {
    Column() {
      TopBar('编辑个人资料', () => this.onBack())
      Scroll() {
        Column() {
          SettingsCard({ content: (): void => this.profileCard() })
        }
        .width('100%')
        .padding({ left: 16, right: 16, top: 8, bottom: 16 })
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
