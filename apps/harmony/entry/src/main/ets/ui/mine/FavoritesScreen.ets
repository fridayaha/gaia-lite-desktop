import { FavoritesViewModel, FavoritesUiState } from './FavoritesViewModel';
import { AppContainer } from '../../di/AppContainer';
import { AppRouter, Routes } from '../nav/Routes';
import { TopBar } from './SettingsCommons';
import { LightTheme } from '../theme/Colors';
import { Typography } from '../theme/Typography';
import { formatRelativeTime } from '../../util/TimeFormat';
import { FavoriteItem } from '../../net/dto/FeedbackDtos';

/**
 * 收藏列表屏。复刻 Android ui/mine/FavoritesScreen.kt。
 * 内容快照（截断 3 行）+ 智能体名 + 时间；点击跳回对应会话。
 */

@Component
export struct FavoritesScreen {
  @Consume('container') container: AppContainer
  @State state: FavoritesUiState = new FavoritesUiState()
  private vm: FavoritesViewModel | null = null
  private listener: (s: FavoritesUiState) => void = (s) => { this.state = s }

  aboutToAppear(): void {
    this.vm = new FavoritesViewModel(this.container.messageFeedbackRepository)
    this.vm.subscribe(this.listener)
    this.vm.load()
  }

  aboutToDisappear(): void {
    if (this.vm !== null) this.vm.unsubscribe(this.listener)
  }

  onBack(): void {
    AppRouter.instance().pop()
  }

  onOpenSession(agentId: string, sessionId: string): void {
    const navParam: Record<string, string> = {
      'agentId': agentId,
      'engineType': '',
      'sessionId': sessionId,
    }
    AppRouter.instance().replace(Routes.CHAT, navParam)
  }

  @Builder
  favoriteRow(item: FavoriteItem): void {
    Column() {
      Text(item.content_snapshot !== null && item.content_snapshot.length > 0
        ? item.content_snapshot : '（无内容）')
        .fontSize(Typography.bodyMedium)
        .fontColor(LightTheme.text)
        .maxLines(3)
        .textOverflow({ overflow: TextOverflow.Ellipsis })
        .width('100%')
      Text(metaLine(item))
        .fontSize(Typography.labelSmall)
        .fontColor(LightTheme.muted)
        .margin({ top: 8 })
        .width('100%')
    }
    .width('100%')
    .padding({ left: 16, right: 16, top: 14, bottom: 14 })
    .backgroundColor(LightTheme.surface)
    .borderRadius(20)
    .margin({ bottom: 12 })
    .onClick(() => {
      if (item.agent_id !== null && item.session_id !== null) {
        this.onOpenSession(item.agent_id, item.session_id)
      }
    })
  }

  build(): void {
    Column() {
      TopBar('收藏', () => this.onBack())

      if (this.state.loading) {
        Column() {
          Text('加载中...')
            .fontColor(LightTheme.muted)
        }
        .width('100%')
        .layoutWeight(1)
        .justifyContent(FlexAlign.Center)
      } else if (this.state.error !== null) {
        Column() {
          Text(this.state.error ?? '加载失败')
            .fontColor(LightTheme.error)
        }
        .width('100%')
        .layoutWeight(1)
        .justifyContent(FlexAlign.Center)
      } else if (this.state.items.length === 0) {
        Column() {
          Text('暂无收藏')
            .fontColor(LightTheme.muted)
        }
        .width('100%')
        .layoutWeight(1)
        .justifyContent(FlexAlign.Center)
      } else {
        Scroll() {
          Column() {
            ForEach(this.state.items, (item: FavoriteItem) => {
              this.favoriteRow(item)
            }, (item: FavoriteItem) => item.id ?? item.message_ref ?? Math.random().toString())
          }
          .padding({ left: 16, right: 16, top: 8, bottom: 8 })
          .width('100%')
          // Scroll 内容不足一屏时默认居中；撑满高度强制顶部对齐
          .constraintSize({ minHeight: '100%' })
        }
        .layoutWeight(1)
        .scrollBar(BarState.Off)
      }
    }
    .width('100%')
    .height('100%')
    .backgroundColor(LightTheme.bg)
  }
}

function metaLine(item: FavoriteItem): string {
  const parts: string[] = []
  if (item.agent_name !== null && item.agent_name.length > 0) parts.push(item.agent_name)
  if (item.created_at !== null && item.created_at.length > 0) {
    parts.push(formatRelativeTime(item.created_at))
  }
  return parts.join(' · ')
}
