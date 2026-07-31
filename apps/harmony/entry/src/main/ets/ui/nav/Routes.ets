/**
 * 路由常量。复刻 Android ui/nav/Routes.kt。
 * ArkUI Navigation 用 NavPathStack + 字符串路由名；参数通过 params 对象传递。
 */
export class Routes {
  static readonly LOGIN = 'login'
  static readonly AGENT_LIST = 'agent_list'
  static readonly MINE = 'mine'
  static readonly WORKSPACE_TAB = 'workspace_tab'
  static readonly SETTINGS = 'settings'
  static readonly SWITCH_ACCOUNT = 'switch_account'
  static readonly PROFILE_EDIT = 'profile_edit'
  static readonly ACCOUNT_SETTINGS = 'account_settings'
  static readonly ABOUT = 'about'
  static readonly VERSION_INFO = 'version_info'
  static readonly UPDATE = 'update'
  static readonly EDIT_TEXT = 'edit_text'
  static readonly EDIT_AVATAR = 'edit_avatar'
  static readonly VERIFY_CONTACT = 'verify_contact'
  static readonly MINE_FAVORITES = 'mine/favorites'
  static readonly CHAT = 'chat'
  static readonly WORKSPACE = 'workspace'
  static readonly FILE_PREVIEW = 'file_preview'
}

/** Chat 路由参数。Index.ets / AgentListScreen / MineScreen 等多处使用。 */
export interface ChatNavParam {
  agentId: string
  engineType: string
  sessionId: string
}

/** EditText 路由参数。 */
export interface EditTextNavParam {
  field: string
}

/** VerifyContact 路由参数。 */
export interface VerifyContactNavParam {
  channel: string
}

/** Workspace 路由参数。 */
export interface WorkspaceNavParam {
  agentId: string
  showBackButton: boolean
}

/** FilePreview 路由参数。 */
export interface FilePreviewNavParam {
  agentId: string
  path: string
}

/**
 * 全局路由控制器。ArkUI Navigation 在某些环境下 NavDestination 不渲染，
 * 改用条件渲染：Index 持有 currentRoute @State，全局单例 AppRouter 暴露
 * navigate/replace/pop，触发 Index 重渲染。
 */
type RouteListener = (route: string, param: Record<string, string>) => void

export class AppRouter {
  private static _inst: AppRouter | null = null
  static instance(): AppRouter {
    if (AppRouter._inst === null) AppRouter._inst = new AppRouter()
    return AppRouter._inst
  }

  private listeners: RouteListener[] = []
  private stack: string[] = []
  private paramStack: Record<string, string>[] = []

  navigate(route: string, param: Record<string, string> = {}): void {
    this.stack.push(route)
    this.paramStack.push(param)
    this.emit(route, param)
  }

  replace(route: string, param: Record<string, string> = {}): void {
    if (this.stack.length > 0) {
      this.stack[this.stack.length - 1] = route
      this.paramStack[this.paramStack.length - 1] = param
    } else {
      this.stack.push(route)
      this.paramStack.push(param)
    }
    this.emit(route, param)
  }

  pop(): void {
    if (this.stack.length > 1) {
      this.stack.pop()
      this.paramStack.pop()
      const route = this.stack[this.stack.length - 1]
      const param = this.paramStack[this.paramStack.length - 1]
      this.emit(route, param)
    }
  }

  currentRoute(): string {
    return this.stack.length > 0 ? this.stack[this.stack.length - 1] : ''
  }

  currentParam(): Record<string, string> {
    return this.paramStack.length > 0 ? this.paramStack[this.paramStack.length - 1] : {}
  }

  subscribe(listener: RouteListener): void {
    this.listeners.push(listener)
  }

  unsubscribe(listener: RouteListener): void {
    this.listeners = this.listeners.filter((l: RouteListener) => l !== listener)
  }

  private emit(route: string, param: Record<string, string>): void {
    for (const l of this.listeners) {
      try { l(route, param) } catch (e) {}
    }
  }
}
