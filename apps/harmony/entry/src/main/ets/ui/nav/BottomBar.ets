import { LightTheme } from '../theme/Colors';
import { Typography } from '../theme/Typography';
import { IconPaths, MIcon } from '../theme/Icons';
import { AppRouter, Routes } from './Routes';

/**
 * 底部 Tab 导航栏。复刻 Android ui/nav/BottomBar.kt。
 * 3 个 Tab：对话 / 云盘(开发者模式=工作区) / 我的。
 * 仅在 AGENT_LIST / WORKSPACE_TAB / MINE 三个路由显示。
 */

export interface BottomTab {
  label: string
  route: string
  icon: string
}

@Component
export struct BottomBar {
  @Prop currentRoute: string = ''
  @Prop developerMode: boolean = false

  build(): void {
    Column() {
      Divider()
        .color(LightTheme.borderVariant)
        .strokeWidth(0.5)

      Row() {
        this.tabItem('对话', Routes.AGENT_LIST, IconPaths.chat)
        this.tabItem(this.developerMode ? '工作区' : '云盘', Routes.WORKSPACE_TAB, IconPaths.folder)
        this.tabItem('我的', Routes.MINE, IconPaths.person)
      }
      .width('100%')
      .height(80)
      .backgroundColor(LightTheme.surface)
    }
    .width('100%')
  }

  @Builder
  tabItem(label: string, route: string, icon: string): void {
    Column() {
      MIcon({
        path: icon,
        iconSize: 24,
        color: this.currentRoute === route ? LightTheme.accent : LightTheme.muted,
      })
      Text(label)
        .fontSize(Typography.labelSmall)
        .fontColor(this.currentRoute === route ? LightTheme.accent : LightTheme.muted)
        .fontWeight(this.currentRoute === route ? FontWeight.Bold : FontWeight.Normal)
        .margin({ top: 2 })
    }
    .layoutWeight(1)
    .height('100%')
    .alignItems(HorizontalAlign.Center)
    .justifyContent(FlexAlign.Center)
    .onClick(() => {
      if (this.currentRoute !== route) {
        AppRouter.instance().replace(route)
      }
    })
  }
}
