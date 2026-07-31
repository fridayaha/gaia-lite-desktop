import { AppContainer } from '../di/AppContainer';
import { AppRouter, Routes } from '../ui/nav/Routes';
import { KeyboardAvoidMode } from '@kit.ArkUI';
import { BottomBar } from '../ui/nav/BottomBar';
import { LoginScreen } from '../ui/login/LoginScreen';
import { AgentListScreen } from '../ui/agentlist/AgentListScreen';
import { ChatScreen } from '../ui/chat/ChatScreen';
import { MineScreen } from '../ui/mine/MineScreen';
import { SettingsScreen } from '../ui/mine/SettingsScreen';
import { FavoritesScreen } from '../ui/mine/FavoritesScreen';
import { AboutScreen } from '../ui/mine/AboutScreen';
import { VersionInfoScreen } from '../ui/mine/VersionInfoScreen';
import { UpdateScreen } from '../ui/mine/UpdateScreen';
import { AccountSettingsScreen } from '../ui/mine/AccountSettingsScreen';
import { SwitchAccountScreen } from '../ui/mine/SwitchAccountScreen';
import { ProfileEditScreen } from '../ui/mine/ProfileEditScreen';
import { EditAvatarScreen } from '../ui/mine/EditAvatarScreen';
import { EditTextFieldScreen } from '../ui/mine/EditTextFieldScreen';
import { VerifyContactScreen } from '../ui/mine/VerifyContactScreen';
import { WorkspaceTabScreen } from '../ui/workspace/WorkspaceTabScreen';
import { WorkspaceScreen } from '../ui/workspace/WorkspaceScreen';
import { FilePreviewScreen } from '../ui/workspace/FilePreviewScreen';
import { logI } from '../util/Logger';

/**
 * 启动页。复刻 Android MainViewModel 决策树：
 * - 无 access_token → LOGIN
 * - 有 access_token + 有 lastAgent → CHAT
 * - 有 access_token + 无 lastAgent → AGENT_LIST
 *
 * Phase 2：mine 全量子屏接入。
 */

@Entry
@Component
struct Index {
  @Provide('container') container: AppContainer = AppStorage.get<AppContainer>('container') as AppContainer
  @State private currentRoute: string = ''
  @State private routeParam: Record<string, string> = {}
  @State private developerMode: boolean = false
  private router: AppRouter = AppRouter.instance()
  private routerListener: (route: string, param: Record<string, string>) => void = (route, param) => {
    this.currentRoute = route
    this.routeParam = param
  }
  private devModeListener: (enabled: boolean) => void = (enabled) => {
    this.developerMode = enabled
  }

  async aboutToAppear(): Promise<void> {
    logI('Index', 'aboutToAppear start')
    // 键盘避让用 RESIZE（压缩窗口）替代系统默认 OFFSET（页面上抬）：
    // 对齐 android adjustResize——顶栏留在原位，聊天翻历史时弹 IME 不打断阅读位置。
    // 在页面侧设置：EntryAbility 里 getMainWindow() 时窗体未就绪，设置会被静默吞掉。
    this.getUIContext().setKeyboardAvoidMode(KeyboardAvoidMode.RESIZE)
    this.router.subscribe(this.routerListener)
    this.container.developerModeStore.subscribe(this.devModeListener)
    try {
      const token = await this.container.tokenStorage.getAccessToken()
      const lastAgent = await this.container.lastAgentStore.get()
      logI('Index', `token=${token !== null ? 'YES' : 'NO'} lastAgent=${lastAgent !== null ? 'YES' : 'NO'}`)
      if (token === null) {
        this.router.replace(Routes.LOGIN)
      } else if (lastAgent !== null) {
        const navParam: Record<string, string> = {
          'agentId': lastAgent.agentId,
          'engineType': lastAgent.engineType ?? '',
          'sessionId': '',
        }
        this.router.replace(Routes.CHAT, navParam)
      } else {
        this.router.replace(Routes.AGENT_LIST)
      }
    } catch (e) {
      logI('Index', `aboutToAppear error: ${e instanceof Error ? e.message : String(e)}`)
    }
  }

  aboutToDisappear(): void {
    this.router.unsubscribe(this.routerListener)
    this.container.developerModeStore.unsubscribe(this.devModeListener)
  }

  private showBottomBar(): boolean {
    return this.currentRoute === Routes.AGENT_LIST
      || this.currentRoute === Routes.MINE
      || this.currentRoute === Routes.WORKSPACE_TAB
  }

  build(): void {
    Column() {
      // 主内容区
      Column() {
        if (this.currentRoute === Routes.LOGIN) {
          LoginScreen()
        } else if (this.currentRoute === Routes.AGENT_LIST) {
          AgentListScreen()
        } else if (this.currentRoute === Routes.CHAT) {
          ChatScreen({ agentId: this.routeParam['agentId'] ?? '' })
        } else if (this.currentRoute === Routes.MINE) {
          MineScreen()
        } else if (this.currentRoute === Routes.SETTINGS) {
          SettingsScreen()
        } else if (this.currentRoute === Routes.MINE_FAVORITES) {
          FavoritesScreen()
        } else if (this.currentRoute === Routes.ABOUT) {
          AboutScreen()
        } else if (this.currentRoute === Routes.VERSION_INFO) {
          VersionInfoScreen()
        } else if (this.currentRoute === Routes.UPDATE) {
          UpdateScreen()
        } else if (this.currentRoute === Routes.ACCOUNT_SETTINGS) {
          AccountSettingsScreen()
        } else if (this.currentRoute === Routes.SWITCH_ACCOUNT) {
          SwitchAccountScreen()
        } else if (this.currentRoute === Routes.PROFILE_EDIT) {
          ProfileEditScreen()
        } else if (this.currentRoute === Routes.EDIT_AVATAR) {
          EditAvatarScreen()
        } else if (this.currentRoute === Routes.EDIT_TEXT) {
          EditTextFieldScreen({ field: this.routeParam['field'] ?? '' })
        } else if (this.currentRoute === Routes.VERIFY_CONTACT) {
          VerifyContactScreen({ channel: this.routeParam['channel'] ?? '' })
        } else if (this.currentRoute === Routes.WORKSPACE_TAB) {
          WorkspaceTabScreen()
        } else if (this.currentRoute === Routes.WORKSPACE) {
          WorkspaceScreen({ agentId: this.routeParam['agentId'] ?? '', showBackButton: true })
        } else if (this.currentRoute === Routes.FILE_PREVIEW) {
          FilePreviewScreen({ agentId: this.routeParam['agentId'] ?? '', path: this.routeParam['path'] ?? '' })
        } else {
          Column() {
            Text('加载中...')
              .fontSize(20)
              .fontColor(0xFF1F2937)
          }
          .width('100%')
          .height('100%')
          .justifyContent(FlexAlign.Center)
          .alignItems(HorizontalAlign.Center)
        }
      }
      .width('100%')
      .layoutWeight(1)

      // 底部 tab 导航
      if (this.showBottomBar()) {
        BottomBar({ currentRoute: this.currentRoute, developerMode: this.developerMode })
      }
    }
    .width('100%')
    .height('100%')
  }
}
