import { MineViewModel } from './MineViewModel';
import { MineUiState } from './MineUiState';
import { AppContainer } from '../../di/AppContainer';
import { TopBar } from './SettingsCommons';
import { AppRouter } from '../nav/Routes';
import { LightTheme } from '../theme/Colors';
import { Typography } from '../theme/Typography';
import { IconPaths, MIcon } from '../theme/Icons';

/**
 * 头像编辑页。复刻 Android ui/mine/EditAvatarScreen.kt。
 * 顶部预览 + 下面 3 列预置头像网格 + 底部「从相册选择」按钮。
 */

@Component
export struct EditAvatarScreen {
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

  onSelectPreset(url: string): void {
    this.vm?.selectPresetAvatar(url)
  }

  build(): void {
    Column() {
      TopBar('更换头像', () => this.onBack())
      Scroll() {
        Column() {
          // 头像预览
          if (this.state.user !== null && this.state.user.avatar_url !== null && this.state.user.avatar_url.length > 0) {
            Image(avatarAbsoluteUrl(this.state.user.avatar_url, this.container))
              .width(120)
              .height(120)
              .borderRadius(60)
              .objectFit(ImageFit.Cover)
          } else {
            Stack() {
              Circle({ width: 120, height: 120 })
                .fill(LightTheme.surfaceVariant)
              Text((this.state.user?.username ?? 'U').charAt(0).toUpperCase())
                .fontSize(Typography.headlineMedium)
                .fontColor(LightTheme.muted)
                .fontWeight(FontWeight.Bold)
            }
            .width(120)
            .height(120)
          }

          Blank().height(8)

          if (this.state.avatarUploading) {
            Text('设置中…')
              .fontSize(Typography.bodySmall)
              .fontColor(LightTheme.muted)
              .margin({ top: 12 })
          } else {
            // 预置头像
            if (this.state.presetAvatars.length > 0) {
              Text('预置头像')
                .fontSize(Typography.titleSmall)
                .fontColor(LightTheme.text)
                .margin({ top: 16, bottom: 12 })
                .width('100%')
              Grid() {
                ForEach(this.state.presetAvatars, (url: string) => {
                  GridItem() {
                    this.presetItem(url)
                  }
                }, (url: string) => url)
              }
              .columnsTemplate('1fr 1fr 1fr')
              .rowsGap(12)
              .columnsGap(12)
              .width('100%')

              Blank().height(8)
            }

            // 从相册选择
            Button('从相册选择')
              .type(ButtonType.Capsule)
              .fontSize(Typography.labelLarge)
              .fontColor(LightTheme.onAccent)
              .backgroundColor(LightTheme.accent)
              .height(44)
              .padding({ left: 24, right: 24 })
              .margin({ top: 16 })
          }

          if (this.state.profileError !== null) {
            Text(this.state.profileError ?? '')
              .fontSize(Typography.labelSmall)
              .fontColor(LightTheme.error)
              .margin({ top: 12 })
          }
        }
        .width('100%')
        .padding(24)
        .alignItems(HorizontalAlign.Center)
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

  @Builder
  presetItem(url: string): void {
    Stack() {
      Image(avatarAbsoluteUrl(url, this.container))
        .width(72)
        .height(72)
        .borderRadius(36)
        .objectFit(ImageFit.Cover)
      if (this.state.user?.avatar_url === url) {
        // 选中态：边框 + 右下勾选
        Column() {
          Circle({ width: 80, height: 80 })
            .fill(0x00000000)
            .border({ width: 2, color: LightTheme.accent })
        }
        .width(80)
        .height(80)

        Stack() {
          Circle({ width: 24, height: 24 })
            .fill(LightTheme.accent)
          MIcon({ path: IconPaths.check, iconSize: 14, color: LightTheme.onAccent })
        }
        .width(24)
        .height(24)
        .position({ x: 56, y: 56 })
      }
    }
    .width(80)
    .height(80)
    .onClick(() => { this.onSelectPreset(url) })
  }
}

function avatarAbsoluteUrl(avatarUrl: string, container: AppContainer): string {
  if (avatarUrl.startsWith('http://') || avatarUrl.startsWith('https://')) return avatarUrl
  const base = container.serverConfig.managerUrl.replace(/\/$/, '').replace(/api\/manager\/?$/, '').replace(/\/$/, '')
  const path = avatarUrl.startsWith('/') ? avatarUrl : `/${avatarUrl}`
  return base + path
}
