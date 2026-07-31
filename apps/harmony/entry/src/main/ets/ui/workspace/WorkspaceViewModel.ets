import { WorkspaceRepository } from '../../repo/WorkspaceRepository';
import { WorkspaceFileEntry } from '../../net/dto/WorkspaceDtos';
import { ApiError } from '../../net/ManagerApi';
import { WorkspaceUiState, WORKSPACE_HIDDEN_DIRS } from './WorkspaceUiState';
import { logE } from '../../util/Logger';

/**
 * 工作区 ViewModel。复刻 Android ui/workspace/WorkspaceViewModel.kt。
 *
 * Phase 1 子集：列表/读/搜索/新建目录/删除；上传走 Uint8Array（Phase 2 接入 picker）。
 * 移动文件 / 多选 / 开发者模式过滤留 Phase 2。
 */
export class WorkspaceViewModel {
  private _state: WorkspaceUiState = new WorkspaceUiState()
  private listeners: Array<(s: WorkspaceUiState) => void> = []
  private repo: WorkspaceRepository

  constructor(repo: WorkspaceRepository) {
    this.repo = repo
  }

  get state(): WorkspaceUiState {
    return this._state
  }

  subscribe(listener: (s: WorkspaceUiState) => void): void {
    this.listeners.push(listener)
    listener(this._state)
  }

  unsubscribe(listener: (s: WorkspaceUiState) => void): void {
    this.listeners = this.listeners.filter((l) => l !== listener)
  }

  private setState(patch: (s: WorkspaceUiState) => void): void {
    const next = this.cloneState()
    patch(next)
    this._state = next
    for (const l of this.listeners) {
      try { l(this._state) } catch (e) { logE('WorkspaceViewModel', `notify failed: ${e}`) }
    }
  }

  private cloneState(): WorkspaceUiState {
    const s = new WorkspaceUiState()
    s.agentId = this._state.agentId
    s.path = this._state.path
    s.stack = this._state.stack.slice()
    s.entries = this._state.entries.slice()
    s.loading = this._state.loading
    s.error = this._state.error
    s.developerMode = this._state.developerMode
    s.searchQuery = this._state.searchQuery
    s.selectionMode = this._state.selectionMode
    s.selectedPaths = new Set<string>(this._state.selectedPaths)
    return s
  }

  setAgentId(agentId: string): void {
    if (this._state.agentId === agentId) return
    this.setState((s) => {
      s.agentId = agentId
      s.path = '.'
      s.stack = ['.']
      s.selectedPaths = new Set<string>()
      s.selectionMode = false
      s.searchQuery = ''
    })
    this.loadDir('.')
  }

  /**
   * 应用开发者模式过滤：根目录下且关闭开发者模式时隐藏 Hermes 内部目录。
   * 非根目录一律展示。
   */
  private applyFilter(entries: WorkspaceFileEntry[]): WorkspaceFileEntry[] {
    if (this._state.developerMode || this._state.path !== '.') return entries
    return entries.filter((e) => !(e.is_dir && WORKSPACE_HIDDEN_DIRS.has(e.name)))
  }

  async loadDir(path: string): Promise<void> {
    const agentId = this._state.agentId
    if (agentId === null) return
    this.setState((s) => { s.loading = true; s.error = null; s.path = path })
    try {
      const list = await this.repo.listFiles(agentId, path)
      this.setState((s) => {
        s.loading = false
        s.entries = this.applyFilter(list.entries)
        // 面包屑回跳：栈中已有该路径则截断到它，否则入栈（对齐 android loadDir 行为）
        const idx = s.stack.indexOf(path)
        s.stack = idx >= 0 ? s.stack.slice(0, idx + 1) : (path === '.' ? ['.'] : s.stack.concat([path]))
        s.selectedPaths = new Set<string>()
        s.selectionMode = false
      })
    } catch (e) {
      const msg = friendlyWorkspaceError(e)
      this.setState((s) => { s.loading = false; s.error = msg })
    }
  }

  async openEntry(entry: WorkspaceFileEntry): Promise<void> {
    if (!entry.is_dir) return
    const newPath = this._state.path === '.' ? entry.name : `${this._state.path}/${entry.name}`
    await this.loadDir(newPath)
  }

  async goBack(): Promise<boolean> {
    const stack = this._state.stack
    if (stack.length <= 1) return false
    const newStack = stack.slice(0, -1)
    const newPath = newStack[newStack.length - 1]
    const agentId = this._state.agentId
    if (agentId === null) return false
    this.setState((s) => { s.loading = true; s.error = null; s.path = newPath; s.stack = newStack })
    try {
      const list = await this.repo.listFiles(agentId, newPath)
      this.setState((s) => {
        s.loading = false
        s.entries = this.applyFilter(list.entries)
        s.selectedPaths = new Set<string>()
        s.selectionMode = false
      })
      return true
    } catch (e) {
      const msg = friendlyWorkspaceError(e)
      this.setState((s) => { s.loading = false; s.error = msg })
      return false
    }
  }

  refresh(): Promise<void> {
    return this.loadDir(this._state.path)
  }

  setSearchQuery(query: string): void {
    this.setState((s) => { s.searchQuery = query })
  }

  async createFolder(name: string, onResult: (ok: boolean, error: string | null) => void = () => {}): Promise<void> {
    const agentId = this._state.agentId
    if (agentId === null) return
    const parent = this._state.path
    try {
      await this.repo.createFolder(agentId, parent, name)
      await this.refresh()
      onResult(true, null)
    } catch (e) {
      const msg = friendlyWorkspaceCreateError(e)
      onResult(false, msg)
    }
  }

  async deleteFile(path: string, onResult: (ok: boolean, error: string | null) => void = () => {}): Promise<void> {
    const agentId = this._state.agentId
    if (agentId === null) return
    try {
      await this.repo.deleteFile(agentId, path)
      this.setState((s) => {
        s.entries = s.entries.filter((e) => e.path !== path)
        s.selectedPaths.delete(path)
      })
      onResult(true, null)
    } catch (e) {
      const msg = e instanceof Error ? (e.message || '删除失败') : '删除失败'
      onResult(false, msg)
    }
  }
}

function friendlyWorkspaceError(e: Object): string {
  if (e instanceof ApiError) {
    if (e.status === 403) return '尚未初始化工作区，请先返回会话发送一条消息后再查看'
    if (e.status === 404) return '路径不存在'
    return `加载失败（${e.status}）`
  }
  if (e instanceof Error) return e.message || '加载失败'
  return '加载失败'
}

function friendlyWorkspaceCreateError(e: Object): string {
  if (e instanceof ApiError) {
    if (e.status === 409) return '文件名或文件夹名已存在'
    if (e.status === 400) return '该名称被系统保留，不可使用'
    return `创建失败（${e.status}）`
  }
  if (e instanceof Error) return e.message || '创建失败'
  return '创建失败'
}
