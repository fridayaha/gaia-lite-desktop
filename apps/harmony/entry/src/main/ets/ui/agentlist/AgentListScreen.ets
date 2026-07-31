import { AgentListViewModel } from './AgentListViewModel';
import { AgentListUiState } from './AgentListUiState';
import { AppContainer } from '../../di/AppContainer';
import { AppRouter, Routes } from '../nav/Routes';
import { LightTheme } from '../theme/Colors';
import { Typography } from '../theme/Typography';
import { IconPaths, MIcon } from '../theme/Icons';
import { AccessibleAgent } from '../../net/dto/AgentDtos';
import { formatRelativeTime } from '../../util/TimeFormat';

/**
 * 智能体列表页。复刻 Android ui/agentlist/AgentListScreen.kt。
 * 顶部「对话」+ 右上搜索图标；列表项：引擎色头像 + 名称 + 引擎 badge + 描述 + 最近访问。
 */

@Component
export struct AgentListScreen {
  @Consume('container') container: AppContainer
  @State state: AgentListUiState = new AgentListUiState()
  @State searchOpen: boolean = false
  private vm: AgentListViewModel | null = null
  private listener: (s: AgentListUiState) => void = (s) => { this.state = s }

  aboutToAppear(): void {
    this.vm = new AgentListViewModel(this.container.agentRepository, this.container.agentContext)
    this.vm.subscribe(this.listener)
    this.vm.load()
  }

  aboutToDisappear(): void {
    if (this.vm !== null) this.vm.unsubscribe(this.listener)
  }

  onAgentClick(agent: AccessibleAgent): void {
    if (this.vm === null) return
    this.vm.selectAgent(agent.id, agent.engine_type)
    const navParam: Record<string, string> = {
      'agentId': agent.id,
      'engineType': agent.engine_type ?? '',
      'sessionId': '',
    }
    AppRouter.instance().replace(Routes.CHAT, navParam)
  }

  openSearch(): void {
    this.searchOpen = true
  }

  closeSearch(): void {
    this.searchOpen = false
    this.vm?.setQuery('')
  }

  // 引擎颜色：HERMES 取 M3 primaryContainer/onPrimaryContainer / DIFY 蓝 / OPENCLAW 青 / CLAUDE_CODE 橙 / 默认灰
  // 与 android AgentListScreen.kt engineColors 保持一致
  engineColors(engineType: string | null): number[] {
    const t = (engineType ?? '').toUpperCase()
    if (t === 'HERMES') return [0xFFEADDFF, 0xFF21005D]
    if (t === 'DIFY') return [0xFFE8F0FE, 0xFF1B64F3]
    if (t === 'OPENCLAW') return [0xFFE0F7FA, 0xFF4DD0E1]
    if (t === 'CLAUDE_CODE') return [0xFFFFEBE5, 0xFFD97757]
    return [LightTheme.surfaceVariant, LightTheme.muted]
  }

  engineLabel(engineType: string | null): string {
    const t = (engineType ?? '').toUpperCase()
    if (t === 'HERMES') return 'Hermes'
    if (t === 'DIFY') return 'Dify'
    if (t === 'OPENCLAW') return 'OpenClaw'
    if (t === 'CLAUDE_CODE') return 'Claude'
    return '—'
  }

  @Builder
  agentAvatar(agent: AccessibleAgent): void {
    Stack() {
      Rect()
        .width(48)
        .height(48)
        .fill(this.engineColors(agent.engine_type)[0])
        .radius(14)
      Text(agent.name.charAt(0).toUpperCase() || '?')
        .fontSize(Typography.titleMedium)
        .fontColor(this.engineColors(agent.engine_type)[1])
        .fontWeight(FontWeight.Bold)
    }
    .width(48)
    .height(48)
  }

  @Builder
  engineBadge(agent: AccessibleAgent): void {
    Text(this.engineLabel(agent.engine_type))
      .fontSize(10)
      .fontWeight(FontWeight.Bold)
      .fontColor(this.engineColors(agent.engine_type)[1])
      .padding({ left: 8, right: 8, top: 2, bottom: 2 })
      .backgroundColor(this.engineColors(agent.engine_type)[0])
      .borderRadius(50)
  }

  @Builder
  agentCard(agent: AccessibleAgent): void {
    Row() {
      this.agentAvatar(agent)
      Column() {
        Row() {
          Text(agent.name)
            .fontSize(Typography.titleMedium)
            .fontWeight(FontWeight.Bold)
            .fontColor(LightTheme.text)
            .maxLines(1)
            .textOverflow({ overflow: TextOverflow.Ellipsis })
            .layoutWeight(1)
          this.engineBadge(agent)
        }
        .width('100%')
        .alignItems(VerticalAlign.Center)

        if (agent.description !== null && agent.description.length > 0) {
          Text(agent.description)
            .fontSize(Typography.bodySmall)
            .fontColor(LightTheme.muted)
            .maxLines(1)
            .textOverflow({ overflow: TextOverflow.Ellipsis })
            .margin({ top: 4 })
            .width('100%')
        }

        if (agent.last_accessed_at !== null && agent.last_accessed_at.length > 0) {
          Row() {
            MIcon({ path: IconPaths.schedule, iconSize: 12, color: LightTheme.muted })
            Text(formatRelativeTime(agent.last_accessed_at))
              .fontSize(Typography.labelSmall)
              .fontColor(LightTheme.muted)
              .margin({ left: 4 })
          }
          .margin({ top: 6 })
          .alignItems(VerticalAlign.Center)
        }
      }
      .layoutWeight(1)
      .margin({ left: 14 })
      .alignItems(HorizontalAlign.Start)
    }
    .width('100%')
    .padding({ left: 16, right: 16, top: 14, bottom: 14 })
    .backgroundColor(LightTheme.surface)
    .borderRadius(16)
    .shadow({ radius: 2, color: 0x0D000000, offsetX: 0, offsetY: 1 })
    .margin({ bottom: 10 })
    .onClick(() => { this.onAgentClick(agent) })
  }

  @Builder
  searchTopBar(): void {
    Row() {
      Row() {
        MIcon({ path: IconPaths.arrowBack, iconSize: 24, color: LightTheme.text })
      }
      .width(40)
      .height(40)
      .alignItems(VerticalAlign.Center)
      .justifyContent(FlexAlign.Center)
      .onClick(() => { this.closeSearch() })

      TextInput({ placeholder: '搜索智能体', text: this.state.query })
        .layoutWeight(1)
        .height(56)
        // 显式浅色底：透明底在部分机型上被渲染成黑色（主题默认填充）
        .backgroundColor(LightTheme.bg)
        .onChange((v) => { this.vm?.setQuery(v) })

      if (this.state.query.length > 0) {
        Row() {
          MIcon({ path: IconPaths.close, iconSize: 24, color: LightTheme.muted })
        }
        .width(40)
        .height(40)
        .alignItems(VerticalAlign.Center)
        .justifyContent(FlexAlign.Center)
        .onClick(() => { this.vm?.setQuery('') })
      }
    }
    .width('100%')
    .padding({ left: 8, right: 8, top: 8, bottom: 8 })
    .alignItems(VerticalAlign.Center)
  }

  build(): void {
    Column() {
      if (this.searchOpen) {
        this.searchTopBar()
      } else {
        // 顶部标题：对话 + 右上搜索
        Row() {
          Blank()
            .width(48)
            .height(48)
          Text('对话')
            .fontSize(Typography.titleMedium)
            .fontWeight(FontWeight.Bold)
            .fontColor(LightTheme.text)
            .layoutWeight(1)
            .textAlign(TextAlign.Center)
          Row() {
            MIcon({ path: IconPaths.search, iconSize: 24, color: LightTheme.text })
          }
          .width(48)
          .height(48)
          .alignItems(VerticalAlign.Center)
          .justifyContent(FlexAlign.Center)
          .onClick(() => { this.openSearch() })
        }
        .width('100%')
        .height(64)
        .alignItems(VerticalAlign.Center)
      }

      if (this.state.loading) {
        Column() {
          LoadingProgress()
            .width(28)
            .height(28)
            .color(LightTheme.accent)
        }
        .width('100%')
        .layoutWeight(1)
        .justifyContent(FlexAlign.Center)
      } else if (this.state.error !== null) {
        Column() {
          Text('加载失败')
            .fontSize(Typography.titleMedium)
            .fontWeight(FontWeight.Medium)
            .fontColor(LightTheme.text)
          Text(this.state.error)
            .fontSize(Typography.bodySmall)
            .fontColor(LightTheme.muted)
            .margin({ top: 6 })
            .textAlign(TextAlign.Center)
          Text('重试')
            .fontSize(Typography.bodyMedium)
            .fontWeight(FontWeight.Bold)
            .fontColor(LightTheme.onAccent)
            .padding({ left: 24, right: 24, top: 10, bottom: 10 })
            .backgroundColor(LightTheme.accent)
            .borderRadius(12)
            .margin({ top: 20 })
            .onClick(() => { this.vm?.load() })
        }
        .width('100%')
        .layoutWeight(1)
        .justifyContent(FlexAlign.Center)
        .padding(48)
      } else if (this.state.filteredAgents.length === 0 && this.state.query.length > 0) {
        // 无搜索结果
        Column() {
          MIcon({ path: IconPaths.search, iconSize: 40, color: LightTheme.muted })
          Text('未找到匹配的智能体')
            .fontSize(Typography.titleMedium)
            .fontWeight(FontWeight.Medium)
            .fontColor(LightTheme.text)
            .margin({ top: 12 })
          Text(`"${this.state.query}"`)
            .fontSize(Typography.bodySmall)
            .fontColor(LightTheme.muted)
            .margin({ top: 4 })
        }
        .width('100%')
        .layoutWeight(1)
        .justifyContent(FlexAlign.Center)
        .padding(48)
      } else if (this.state.agents.length === 0) {
        // 空态
        Column() {
          Stack() {
            Rect()
              .width(64)
              .height(64)
              .fill(LightTheme.surfaceVariant)
              .radius(20)
            MIcon({ path: IconPaths.person, iconSize: 32, color: LightTheme.muted })
          }
          .width(64)
          .height(64)
          Text('暂无可用智能体')
            .fontSize(Typography.titleMedium)
            .fontWeight(FontWeight.Medium)
            .fontColor(LightTheme.text)
            .margin({ top: 16 })
          Text('请联系管理员为您开通智能体访问权限')
            .fontSize(Typography.bodyMedium)
            .fontColor(LightTheme.muted)
            .margin({ top: 6 })
            .textAlign(TextAlign.Center)
        }
        .width('100%')
        .layoutWeight(1)
        .justifyContent(FlexAlign.Center)
        .padding(48)
      } else {
        Scroll() {
          Column() {
            ForEach(this.state.filteredAgents, (agent: AccessibleAgent) => {
              this.agentCard(agent)
            }, (agent: AccessibleAgent) => agent.id)
          }
          .padding({ left: 16, right: 16, top: 4, bottom: 4 })
          .width('100%')
          // Scroll 内容不足一屏时默认居中；撑满高度强制顶部对齐（对齐 android LazyColumn）
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
