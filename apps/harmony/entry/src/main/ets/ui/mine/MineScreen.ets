import { MineViewModel } from './MineViewModel';
import { MineUiState } from './MineUiState';
import { AppContainer } from '../../di/AppContainer';
import { AppRouter, Routes } from '../nav/Routes';
import { LightTheme } from '../theme/Colors';
import { Typography } from '../theme/Typography';
import { IconPaths, MIcon } from '../theme/Icons';
import { formatRelativeTime } from '../../util/TimeFormat';
import { FavoriteItem } from '../../net/dto/FeedbackDtos';
import { UserInfo } from '../../net/dto/AuthDtos';

/**
 * 「我的」主屏。复刻 Android ui/mine/MineScreen.kt。
 * 顶部仅右上角齿轮（无「我的」标题），中间 头像 + 名 + 编辑资料按钮（胶囊形），
 * 下方「收藏」卡片含「查看全部」链接（近 10 条预览）。
 */

@Component
export struct MineScreen {
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

  onOpenSettings(): void {
    AppRouter.instance().navigate(Routes.SETTINGS)
  }

  onEditProfile(): void {
    AppRouter.instance().navigate(Routes.PROFILE_EDIT)
  }

  onOpenFavorites(): void {
    AppRouter.instance().navigate(Routes.MINE_FAVORITES)
  }

  onOpenSession(agentId: string, sessionId: string): void {
    const navParam: Record<string, string> = {
      'agentId': agentId,
      'engineType': '',
      'sessionId': sessionId,
    }
    AppRouter.instance().navigate(Routes.CHAT, navParam)
  }

  private avatarUrl(): string | null {
    const u = this.state.user
    if (u === null || u.avatar_url === null || u.avatar_url.length === 0) return null
    return u.avatar_url
  }

  private usernameInitial(): string {
    const u = this.state.user
    if (u !== null && u.username.length > 0) return u.username.charAt(0).toUpperCase()
    return 'U'
  }

  @Builder
  avatarBlock(): void {
    Column() {
      if (this.avatarUrl() !== null) {
        Image(avatarAbsoluteUrl(this.avatarUrl() ?? '', this.container))
          .width(80)
          .height(80)
          .borderRadius(40)
          .objectFit(ImageFit.Cover)
      } else {
        Stack() {
          Circle({ width: 80, height: 80 })
            .fill(LightTheme.accent)
          Text(this.usernameInitial())
            .fontSize(Typography.headlineMedium)
            .fontColor(LightTheme.onAccent)
            .fontWeight(FontWeight.Bold)
        }
        .width(80)
        .height(80)
      }
      Text(this.state.user !== null ? (this.state.user?.username ?? '') : '未登录')
        .fontSize(Typography.titleLarge)
        .fontWeight(FontWeight.Bold)
        .fontColor(LightTheme.text)
        .margin({ top: 12 })
      if (subtitleOf(this.state.user).length > 0) {
        Text(subtitleOf(this.state.user))
          .fontSize(Typography.bodySmall)
          .fontColor(LightTheme.muted)
          .margin({ top: 4 })
      }
      // 编辑资料按钮（胶囊形 + 右箭头）
      Row() {
        Text('编辑个人资料')
          .fontSize(Typography.labelLarge)
          .fontColor(LightTheme.muted)
        MIcon({ path: IconPaths.keyboardArrowRight, iconSize: 16, color: LightTheme.muted })
          .margin({ left: 4 })
      }
      .padding({ left: 18, right: 18, top: 8, bottom: 8 })
      .borderRadius(50)
      .backgroundColor(LightTheme.surfaceVariant)
      .margin({ top: 16 })
      .onClick(() => { this.onEditProfile() })
    }
    .width('100%')
    .padding({ top: 8, bottom: 24 })
    .alignItems(HorizontalAlign.Center)
  }

  @Builder
  favoriteRow(item: FavoriteItem): void {
    Column() {
      Text(item.content_snapshot !== null && item.content_snapshot.length > 0
        ? item.content_snapshot : '（无内容）')
        .fontSize(Typography.bodySmall)
        .fontColor(LightTheme.text)
        .maxLines(1)
        .textOverflow({ overflow: TextOverflow.Ellipsis })
        .width('100%')
      Text(metaLine(item))
        .fontSize(Typography.labelSmall)
        .fontColor(LightTheme.muted)
        .margin({ top: 2 })
        .width('100%')
    }
    .width('100%')
    .padding({ left: 16, right: 16, top: 8, bottom: 8 })
    .onClick(() => {
      if (item.agent_id !== null && item.session_id !== null) {
        this.onOpenSession(item.agent_id, item.session_id)
      }
    })
  }

  @Builder
  favoritesCard(): void {
    Column() {
      // 标题行：书签图标 + 收藏 + 查看全部 链接
      Row() {
        MIcon({ path: IconPaths.bookmark, iconSize: 20, color: LightTheme.muted })
        Text('收藏')
          .fontSize(Typography.titleSmall)
          .fontWeight(FontWeight.Bold)
          .fontColor(LightTheme.text)
          .margin({ left: 8 })
          .layoutWeight(1)
        Row() {
          Text('查看全部')
            .fontSize(Typography.labelLarge)
            .fontColor(LightTheme.accent)
          MIcon({ path: IconPaths.keyboardArrowRight, iconSize: 16, color: LightTheme.accent })
            .margin({ left: 4 })
        }
        .onClick(() => { this.onOpenFavorites() })
      }
      .width('100%')
      .padding({ left: 16, right: 16, top: 6, bottom: 6 })
      .alignItems(VerticalAlign.Center)

      if (this.state.recentFavorites.length === 0) {
        Text('暂无收藏')
          .fontSize(Typography.bodySmall)
          .fontColor(LightTheme.muted)
          .padding({ left: 16, right: 16, top: 12, bottom: 12 })
          .width('100%')
      } else {
        ForEach(this.state.recentFavorites, (item: FavoriteItem) => {
          this.favoriteRow(item)
        }, (item: FavoriteItem) => item.id ?? item.message_ref ?? Math.random().toString())
      }
    }
    .width('100%')
    .backgroundColor(LightTheme.surface)
    .borderRadius(20)
    .margin({ left: 16, right: 16, bottom: 24 })
    .padding({ top: 8, bottom: 8 })
    .alignItems(HorizontalAlign.Start)
  }

  build(): void {
    Column() {
      // 顶部空白栏 + 右上角设置齿轮
      Row() {
        Blank()
        Row() {
          MIcon({ path: IconPaths.settings, iconSize: 24, color: LightTheme.text })
        }
        .width(48)
        .height(48)
        .alignItems(VerticalAlign.Center)
        .justifyContent(FlexAlign.Center)
        .onClick(() => { this.onOpenSettings() })
      }
      .width('100%')
      .height(64)
      .padding({ left: 4, right: 4 })
      .alignItems(VerticalAlign.Center)

      // 内容区
      Scroll() {
        Column() {
          this.avatarBlock()
          this.favoritesCard()
        }
        .width('100%')
        .alignItems(HorizontalAlign.Center)
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

function subtitleOf(user: UserInfo | null): string {
  if (user === null) return ''
  const rn = user.real_name
  if (rn !== null && rn.length > 0) return rn
  const em = user.email
  if (em !== null && em.length > 0) return em
  return ''
}

function metaLine(item: FavoriteItem): string {
  const parts: string[] = []
  if (item.agent_name !== null && item.agent_name.length > 0) parts.push(item.agent_name)
  if (item.created_at !== null && item.created_at.length > 0) {
    parts.push(formatRelativeTime(item.created_at))
  }
  return parts.join(' · ')
}

function avatarAbsoluteUrl(avatarUrl: string, container: AppContainer): string {
  if (avatarUrl.startsWith('http://') || avatarUrl.startsWith('https://')) {
    return avatarUrl
  }
  // 对齐 android：avatar_url 挂在 host 根路径，去掉 manager base 的 api/manager 前缀
  const base = container.serverConfig.managerUrl.replace(/\/$/, '').replace(/api\/manager\/?$/, '').replace(/\/$/, '')
  const path = avatarUrl.startsWith('/') ? avatarUrl : `/${avatarUrl}`
  return base + path
}
