import { WorkspaceTabViewModel, WorkspaceTabUiState } from './WorkspaceTabViewModel';
import { WorkspaceScreen } from './WorkspaceScreen';
import { AppContainer } from '../../di/AppContainer';
import { AppRouter, Routes } from '../nav/Routes';
import { LightTheme } from '../theme/Colors';
import { Typography } from '../theme/Typography';
import { IconPaths, MIcon } from '../theme/Icons';
import { AccessibleAgent } from '../../net/dto/AgentDtos';

/**
 * 工作区 Tab 屏。复刻 Android ui/workspace/WorkspaceTabScreen.kt。
 * agent 选择器作为 WorkspaceScreen 顶栏标题（点击展开下拉菜单），内容区内嵌 WorkspaceScreen。
 */

@Component
export struct WorkspaceTabScreen {
  @Consume('container') container: AppContainer
  @State state: WorkspaceTabUiState = new WorkspaceTabUiState()
  @State showAgentPicker: boolean = false
  private vm: WorkspaceTabViewModel | null = null
  private listener: (s: WorkspaceTabUiState) => void = (s) => { this.state = s }

  aboutToAppear(): void {
    this.vm = new WorkspaceTabViewModel(
      this.container.agentRepository,
      this.container.agentContext,
      this.container.lastAgentStore,
    )
    this.vm.subscribe(this.listener)
    this.vm.load()
  }

  aboutToDisappear(): void {
    if (this.vm !== null) this.vm.unsubscribe(this.listener)
  }

  onOpenFile(agentId: string, path: string): void {
    const navParam: Record<string, string> = { 'agentId': agentId, 'path': path }
    AppRouter.instance().navigate(Routes.FILE_PREVIEW, navParam)
  }

  @Builder
  agentPickerItem(agent: AccessibleAgent): void {
    Row() {
      Column() {
        Text(agent.name.charAt(0).toUpperCase() || '?')
          .fontSize(Typography.labelMedium)
          .fontWeight(FontWeight.Bold)
          .fontColor(LightTheme.onAccentContainer)
      }
      .width(28)
      .height(28)
      .backgroundColor(LightTheme.accentContainer)
      .borderRadius(8)
      .justifyContent(FlexAlign.Center)
      Text(agent.name)
        .fontSize(Typography.bodyLarge)
        .fontColor(LightTheme.text)
        .maxLines(1)
        .textOverflow({ overflow: TextOverflow.Ellipsis })
        .layoutWeight(1)
        .margin({ left: 12 })
    }
    .width('100%')
    .height(48)
    .padding({ left: 12, right: 12 })
    .alignItems(VerticalAlign.Center)
    .onClick(() => {
      this.vm?.selectAgent(agent.id)
      this.showAgentPicker = false
    })
  }

  build(): void {
    Column() {
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
          Text(this.state.error ?? '')
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
        .padding({ left: 48, right: 48 })
      } else if (this.state.agents.length === 0 || this.state.selectedAgentId === null) {
        Column() {
          Text('暂无可访问的智能体')
            .fontSize(Typography.titleMedium)
            .fontWeight(FontWeight.Medium)
            .fontColor(LightTheme.text)
          Text('请联系管理员为您开通智能体访问权限')
            .fontSize(Typography.bodyMedium)
            .fontColor(LightTheme.muted)
            .margin({ top: 6 })
            .textAlign(TextAlign.Center)
        }
        .width('100%')
        .layoutWeight(1)
        .justifyContent(FlexAlign.Center)
        .padding({ left: 48, right: 48 })
      } else {
        Stack() {
          // 内嵌 WorkspaceScreen：标题 = agent 选择器（点击展开下拉）
          WorkspaceScreen({
            agentId: this.state.selectedAgentId,
            showBackButton: false,
            titleText: this.selectedAgentName(),
            onTitleClick: () => { this.showAgentPicker = !this.showAgentPicker },
          })

          // agent 下拉菜单（对齐 android ExposedDropdownMenu：宽 240、锚在顶栏标题下方、外点关闭）
          if (this.showAgentPicker) {
            Column() {
              Blank()
                .width('100%')
                .height(64)
                .onClick(() => { this.showAgentPicker = false })
              Column() {
                ForEach(this.state.agents, (agent: AccessibleAgent) => {
                  this.agentPickerItem(agent)
                }, (agent: AccessibleAgent) => agent.id)
              }
              .width(240)
              .backgroundColor(LightTheme.surface)
              .borderRadius(4)
              .shadow({ radius: 8, color: 0x26000000, offsetX: 0, offsetY: 3 })
              .margin({ left: 16 })
              Blank()
                .layoutWeight(1)
                .width('100%')
                .onClick(() => { this.showAgentPicker = false })
            }
            .width('100%')
            .height('100%')
            .alignItems(HorizontalAlign.Start)
          }
        }
        .layoutWeight(1)
        .width('100%')
      }
    }
    .width('100%')
    .height('100%')
    .backgroundColor(LightTheme.bg)
  }

  private selectedAgentName(): string {
    const id = this.state.selectedAgentId
    if (id === null) return '选择智能体'
    const a = this.state.agents.find((x) => x.id === id)
    return a !== undefined ? a.name : '选择智能体'
  }
}
