import { MineViewModel } from './MineViewModel';
import { MineUiState } from './MineUiState';
import { AppContainer } from '../../di/AppContainer';
import { TopBar } from './SettingsCommons';
import { AppRouter, Routes } from '../nav/Routes';
import { LightTheme } from '../theme/Colors';
import { Typography } from '../theme/Typography';
import { IconPaths, MIcon } from '../theme/Icons';
import { SavedAccount } from '../../repo/AccountStore';

/**
 * 切换账号页。复刻 Android ui/mine/SwitchAccountScreen.kt。
 * 顶部「当前账号」+ 下面「其他账号」列表（点击切换/移除）+ 底部「添加新账号」。
 */

@Component
export struct SwitchAccountScreen {
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

  onAddAccount(): void {
    this.vm?.logout(() => {
      AppRouter.instance().replace(Routes.LOGIN)
    })
  }

  onSwitch(acc: SavedAccount): void {
    this.vm?.switchAccount(acc.id, () => {
      AppRouter.instance().pop()
    })
  }

  onForget(acc: SavedAccount): void {
    this.vm?.forgetAccount(acc.id)
  }

  build(): void {
    Column() {
      TopBar('切换账号', () => this.onBack())
      Scroll() {
        Column() {
          // 当前账号
          Text('当前账号')
            .fontSize(Typography.labelMedium)
            .fontColor(LightTheme.muted)
            .margin({ left: 8, top: 8, bottom: 8 })
            .width('100%')
          this.currentAccountCard()

          Blank().height(8)

          // 其他账号
          if (this.otherAccounts().length > 0) {
            Text('其他账号')
              .fontSize(Typography.labelMedium)
              .fontColor(LightTheme.muted)
              .margin({ left: 8, top: 4, bottom: 8 })
              .width('100%')
            ForEach(this.otherAccounts(), (acc: SavedAccount) => {
              this.accountCard(acc)
            }, (acc: SavedAccount) => acc.id)
          }

          // 添加新账号
          this.addAccountCard()
        }
        .width('100%')
        .padding({ left: 16, right: 16, top: 8, bottom: 16 })
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

  private otherAccounts(): SavedAccount[] {
    const currentId = this.state.user?.id
    return this.state.savedAccounts.filter((a) => a.id !== currentId)
  }

  @Builder
  avatar(avatarUrl: string | null | undefined, username: string, isCurrent: boolean): void {
    if (avatarUrl !== null && avatarUrl !== undefined && avatarUrl.length > 0) {
      Image(avatarAbsoluteUrl(avatarUrl, this.container))
        .width(44)
        .height(44)
        .borderRadius(22)
        .objectFit(ImageFit.Cover)
    } else {
      Stack() {
        Circle({ width: 44, height: 44 })
          .fill(isCurrent ? LightTheme.accent : LightTheme.surfaceVariant)
        Text(username.charAt(0).toUpperCase() || 'U')
          .fontSize(Typography.titleMedium)
          .fontWeight(FontWeight.Bold)
          .fontColor(isCurrent ? LightTheme.onAccent : LightTheme.muted)
      }
      .width(44)
      .height(44)
    }
  }

  @Builder
  currentAccountCard(): void {
    Row() {
      // 内联头像：@Builder 值参数快照不随 @State 刷新（见 MineScreen avatarBlock 修复）
      if (this.state.user !== null && this.state.user.avatar_url !== null && this.state.user.avatar_url.length > 0) {
        Image(avatarAbsoluteUrl(this.state.user.avatar_url, this.container))
          .width(44)
          .height(44)
          .borderRadius(22)
          .objectFit(ImageFit.Cover)
      } else {
        Stack() {
          Circle({ width: 44, height: 44 })
            .fill(LightTheme.accent)
          Text((this.state.user?.username ?? 'U').charAt(0).toUpperCase() || 'U')
            .fontSize(Typography.titleMedium)
            .fontWeight(FontWeight.Bold)
            .fontColor(LightTheme.onAccent)
        }
        .width(44)
        .height(44)
      }
      Column() {
        Text(this.state.user?.username ?? '')
          .fontSize(Typography.bodyLarge)
          .fontWeight(FontWeight.Medium)
          .fontColor(LightTheme.text)
        if (this.state.user !== null && this.state.user.email !== null && this.state.user.email.length > 0) {
          Row() {
            MIcon({ path: IconPaths.mail, iconSize: 12, color: LightTheme.muted })
            Text(this.state.user.email)
              .fontSize(Typography.bodySmall)
              .fontColor(LightTheme.muted)
              .margin({ left: 4 })
          }
          .margin({ top: 2 })
          .alignItems(VerticalAlign.Center)
        }
      }
      .layoutWeight(1)
      .alignItems(HorizontalAlign.Start)
      .margin({ left: 12 })
      MIcon({ path: IconPaths.checkCircle, iconSize: 20, color: LightTheme.accent })
    }
    .width('100%')
    .padding({ left: 16, right: 16, top: 14, bottom: 14 })
    .backgroundColor(LightTheme.surface)
    .borderRadius(20)
    .alignItems(VerticalAlign.Center)
  }

  @Builder
  accountCard(acc: SavedAccount): void {
    Row() {
      this.avatar(acc.avatar_url, acc.username, false)
      Column() {
        Text(acc.username)
          .fontSize(Typography.bodyLarge)
          .fontWeight(FontWeight.Medium)
          .fontColor(LightTheme.text)
        if (acc.email !== null && acc.email.length > 0) {
          Row() {
            MIcon({ path: IconPaths.mail, iconSize: 12, color: LightTheme.muted })
            Text(acc.email)
              .fontSize(Typography.bodySmall)
              .fontColor(LightTheme.muted)
              .margin({ left: 4 })
          }
          .margin({ top: 2 })
          .alignItems(VerticalAlign.Center)
        }
      }
      .layoutWeight(1)
      .alignItems(HorizontalAlign.Start)
      .margin({ left: 12 })
      // 移除账号按钮
      Row() {
        MIcon({ path: IconPaths.close, iconSize: 18, color: LightTheme.muted })
      }
      .width(36)
      .height(36)
      .alignItems(VerticalAlign.Center)
      .justifyContent(FlexAlign.Center)
      .onClick(() => { this.onForget(acc) })
    }
    .width('100%')
    .padding({ left: 16, right: 16, top: 14, bottom: 14 })
    .backgroundColor(LightTheme.surface)
    .borderRadius(20)
    .alignItems(VerticalAlign.Center)
    .opacity(this.state.switchingAccount ? 0.5 : 1)
    .margin({ bottom: 12 })
    .onClick(() => { if (!this.state.switchingAccount) this.onSwitch(acc) })
  }

  @Builder
  addAccountCard(): void {
    Row() {
      MIcon({ path: IconPaths.add, iconSize: 24, color: LightTheme.accent })
      Text('添加新账号')
        .fontSize(Typography.bodyLarge)
        .fontColor(LightTheme.accent)
        .layoutWeight(1)
        .margin({ left: 12 })
      MIcon({ path: IconPaths.keyboardArrowRight, iconSize: 20, color: LightTheme.muted })
    }
    .width('100%')
    .padding({ left: 16, right: 16, top: 14, bottom: 14 })
    .backgroundColor(LightTheme.surface)
    .borderRadius(20)
    .alignItems(VerticalAlign.Center)
    .margin({ top: 12 })
    .onClick(() => { this.onAddAccount() })
  }
}

function avatarAbsoluteUrl(avatarUrl: string, container: AppContainer): string {
  if (avatarUrl.startsWith('http://') || avatarUrl.startsWith('https://')) return avatarUrl
  const base = container.serverConfig.managerUrl.replace(/\/$/, '').replace(/api\/manager\/?$/, '').replace(/\/$/, '')
  const path = avatarUrl.startsWith('/') ? avatarUrl : `/${avatarUrl}`
  return base + path
}
