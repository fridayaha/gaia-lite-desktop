import { WorkspaceFileEntry } from '../../net/dto/WorkspaceDtos';

/**
 * 工作区 UI 状态。复刻 Android ui/workspace/WorkspaceUiState.kt。
 */
export class WorkspaceUiState {
  agentId: string | null = null
  path: string = '.'
  stack: string[] = ['.']
  entries: WorkspaceFileEntry[] = []
  loading: boolean = false
  error: string | null = null
  developerMode: boolean = false
  searchQuery: string = ''
  selectionMode: boolean = false
  selectedPaths: Set<string> = new Set<string>()

  /** 经搜索过滤后的条目（保持目录在前的排序）。 */
  get filteredEntries(): WorkspaceFileEntry[] {
    if (this.searchQuery.length === 0) return this.entries
    const q = this.searchQuery.toLowerCase()
    return this.entries.filter((e) => e.name.toLowerCase().includes(q))
  }
}

/**
 * 工作区根目录下的 Hermes 内部目录黑名单——开发者模式关闭时隐藏。
 * 复刻 Android WORKSPACE_HIDDEN_DIRS。
 */
export const WORKSPACE_HIDDEN_DIRS: Set<string> = new Set<string>([
  'audio_cache',
  'cache',
  'cron',
  'home',
  'hooks',
  'image_cache',
  'logs',
  'memories',
  'pairing',
  'plans',
  'platforms',
  'plugins',
  'sandboxes',
  'sessions',
  'skills',
  'skins',
  'workspace',
])
