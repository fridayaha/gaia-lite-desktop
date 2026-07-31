import { MineViewModel } from './MineViewModel';
import { MineUiState } from './MineUiState';
import { AppContainer } from '../../di/AppContainer';
import { TopBar, SettingsCard } from './SettingsCommons';
import { AppRouter } from '../nav/Routes';
import { LightTheme } from '../theme/Colors';
import { Typography } from '../theme/Typography';
import { AppReleaseLatest } from '../../net/dto/AppReleaseDtos';

/**
 * 版本详情页。复刻 Android ui/mine/VersionInfoScreen.kt。
 * 当前版本号 + 版本描述（无对应 release 时 fallback 到 latest 描述）。
 */

@Component
export struct VersionInfoScreen {
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

  private description(): string {
    const rel: AppReleaseLatest | null = this.state.currentVersionRelease ?? this.state.latestRelease
    if (rel === null) return '暂无版本描述'
    const d = rel.description
    if (d !== null && d.length > 0) return d
    return '暂无版本描述'
  }

  @Builder
  versionCard(): void {
    Column() {
      Text('当前版本')
        .fontSize(Typography.labelMedium)
        .fontColor(LightTheme.muted)
      Text(`v${this.state.appVersion}`)
        .fontSize(Typography.titleMedium)
        .fontWeight(FontWeight.Medium)
        .fontColor(LightTheme.text)
        .margin({ top: 12 })
      Blank()
        .height(4)
      Text('版本描述')
        .fontSize(Typography.labelMedium)
        .fontColor(LightTheme.muted)
      Text(this.description())
        .fontSize(Typography.bodyMedium)
        .fontColor(LightTheme.text)
        .margin({ top: 12 })
    }
    .width('100%')
    .padding(20)
    .alignItems(HorizontalAlign.Start)
  }

  build(): void {
    Column() {
      TopBar('版本', () => this.onBack())
      Scroll() {
        Column() {
          SettingsCard({ content: (): void => this.versionCard() })
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
