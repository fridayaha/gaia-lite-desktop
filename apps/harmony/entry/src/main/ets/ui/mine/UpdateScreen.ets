import { MineViewModel } from './MineViewModel';
import { MineUiState } from './MineUiState';
import { AppContainer } from '../../di/AppContainer';
import { TopBar, SettingsCard } from './SettingsCommons';
import { AppRouter } from '../nav/Routes';
import { LightTheme } from '../theme/Colors';
import { Typography } from '../theme/Typography';

/**
 * 检查更新页。复刻 Android ui/mine/UpdateScreen.kt。
 * 当前版本卡片 + 最新版本信息卡片；支持下载更新。
 */

@Component
export struct UpdateScreen {
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
    this.vm.checkForUpdate()
  }

  aboutToDisappear(): void {
    if (this.vm !== null) this.vm.unsubscribe(this.listener)
  }

  onBack(): void {
    AppRouter.instance().pop()
  }

  @Builder
  currentVersionCard(): void {
    Column() {
      Text('当前版本')
        .fontSize(Typography.labelMedium)
        .fontColor(LightTheme.muted)
      Text(`v${this.state.appVersion}`)
        .fontSize(Typography.titleMedium)
        .fontWeight(FontWeight.Medium)
        .fontColor(LightTheme.text)
        .margin({ top: 8 })
    }
    .width('100%')
    .padding(20)
    .alignItems(HorizontalAlign.Start)
  }

  @Builder
  latestVersionCard(): void {
    Column() {
      if (this.state.checkingUpdate) {
        Text('正在检查更新…')
          .fontSize(Typography.bodyMedium)
          .fontColor(LightTheme.muted)
          .margin({ top: 24, bottom: 24 })
      } else if (this.state.updateError !== null) {
        Text(this.state.updateError ?? '')
          .fontSize(Typography.bodyMedium)
          .fontColor(LightTheme.error)
          .margin({ top: 24, bottom: 8 })
        Button('重试')
          .type(ButtonType.Capsule)
          .onClick(() => { this.vm?.checkForUpdate() })
          .margin({ bottom: 24 })
      } else if (this.state.updateAvailable && this.state.latestRelease !== null) {
        this.updateAvailableContent()
      } else if (this.state.upToDate) {
        Text('已是最新版本')
          .fontSize(Typography.bodyLarge)
          .fontWeight(FontWeight.Medium)
          .fontColor(LightTheme.text)
          .margin({ top: 24 })
        if (this.state.latestRelease !== null && this.state.latestRelease.version !== null) {
          Text(`线上最新版本：v${this.state.latestRelease.version}`)
            .fontSize(Typography.bodySmall)
            .fontColor(LightTheme.muted)
            .margin({ top: 8, bottom: 24 })
        }
      } else {
        Text('检查更新失败')
          .fontSize(Typography.bodyMedium)
          .fontColor(LightTheme.error)
          .margin({ top: 24, bottom: 8 })
        Button('重试')
          .type(ButtonType.Capsule)
          .onClick(() => { this.vm?.checkForUpdate() })
          .margin({ bottom: 24 })
      }
    }
    .width('100%')
    .padding(20)
    .alignItems(HorizontalAlign.Center)
  }

  build(): void {
    Column() {
      TopBar('检查更新', () => this.onBack())
      Scroll() {
        Column() {
          SettingsCard({ content: (): void => this.currentVersionCard() })

          Blank()
            .height(12)

          SettingsCard({ content: (): void => this.latestVersionCard() })
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

  @Builder
  updateAvailableContent(): void {
    // @Builder 值参数快照不随 @State 刷新（见 MineScreen avatarBlock 修复），release 从 state 直读
    if (this.state.latestRelease !== null) {
      Text('发现新版本')
        .fontSize(Typography.bodyLarge)
        .fontWeight(FontWeight.Medium)
        .fontColor(LightTheme.text)
        .margin({ top: 24, bottom: 8 })
      Text(this.state.latestRelease.version !== null ? `v${this.state.latestRelease.version}` : '最新版本')
        .fontSize(Typography.titleMedium)
        .fontWeight(FontWeight.Medium)
        .fontColor(LightTheme.accent)
        .margin({ bottom: 8 })
      if (this.state.latestRelease.size !== null && (this.state.latestRelease.size ?? 0) > 0) {
        Text(formatSize(this.state.latestRelease.size ?? 0))
          .fontSize(Typography.bodySmall)
          .fontColor(LightTheme.muted)
          .margin({ bottom: 8 })
      }
      if (this.state.latestRelease.description !== null && (this.state.latestRelease.description ?? '').length > 0) {
        Text(this.state.latestRelease.description ?? '')
          .fontSize(Typography.bodySmall)
          .fontColor(LightTheme.muted)
          .margin({ bottom: 12 })
      }
    }
    if (this.state.downloadProgress !== null) {
      Progress({ value: Math.floor(this.state.downloadProgress * 100), total: 100, type: ProgressType.Linear })
        .width('100%')
        .margin({ top: 4, bottom: 4 })
      Text(this.state.downloadProgress >= 1.0 ? '下载完成，等待安装…' : `下载中… ${Math.floor((this.state.downloadProgress ?? 0) * 100)}%`)
        .fontSize(Typography.bodySmall)
        .fontColor(LightTheme.muted)
        .margin({ bottom: 24 })
    } else {
      Button('下载更新')
        .type(ButtonType.Capsule)
        .onClick(() => { this.vm?.startUpdateDownload() })
        .margin({ bottom: 24 })
    }
  }
}

function formatSize(bytes: number): string {
  const mb = bytes / 1024.0 / 1024.0
  if (mb >= 1) return `约 ${mb.toFixed(1)} MB`
  return `${Math.floor(bytes / 1024)} KB`
}
