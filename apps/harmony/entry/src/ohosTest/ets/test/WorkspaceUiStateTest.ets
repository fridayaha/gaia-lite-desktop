import { describe, it, expect } from '@ohos/hypium';
import { WorkspaceUiState, WORKSPACE_HIDDEN_DIRS } from '../../../main/ets/ui/workspace/WorkspaceUiState';
import { WorkspaceFileEntry } from '../../../main/ets/net/dto/WorkspaceDtos';

/**
 * WorkspaceUiState.filteredEntries 单元测试。
 * 验证 searchQuery 大小写不敏感过滤 + 空查询时原样返回。
 */
export function registerWorkspaceUiStateTests(): void {
  function mkEntry(name: string, isDir: boolean = false): WorkspaceFileEntry {
    const e = new WorkspaceFileEntry()
    e.name = name
    e.is_dir = isDir
    e.size = 0
    return e
  }

  describe('WorkspaceUiState.filteredEntries', () => {
    it('empty searchQuery returns all entries', 0, () => {
      const s = new WorkspaceUiState()
      s.entries = [mkEntry('a.txt'), mkEntry('b.md'), mkEntry('c/')]
      expect(s.filteredEntries.length).assertEqual(3)
    })

    it('filters by case-insensitive substring match', 0, () => {
      const s = new WorkspaceUiState()
      s.entries = [mkEntry('README.md'), mkEntry('notes.txt'), mkEntry('data/')]
      s.searchQuery = 'readme'
      expect(s.filteredEntries.length).assertEqual(1)
      expect(s.filteredEntries[0].name).assertEqual('README.md')
    })

    it('multiple matches returned in original order', 0, () => {
      const s = new WorkspaceUiState()
      s.entries = [mkEntry('a.md'), mkEntry('b.md'), mkEntry('c.txt')]
      s.searchQuery = '.md'
      expect(s.filteredEntries.length).assertEqual(2)
      expect(s.filteredEntries[0].name).assertEqual('a.md')
      expect(s.filteredEntries[1].name).assertEqual('b.md')
    })

    it('no match returns empty list', 0, () => {
      const s = new WorkspaceUiState()
      s.entries = [mkEntry('a.md')]
      s.searchQuery = 'zzz'
      expect(s.filteredEntries.length).assertEqual(0)
    })

    it('empty entries + non-empty query returns empty', 0, () => {
      const s = new WorkspaceUiState()
      s.entries = []
      s.searchQuery = 'anything'
      expect(s.filteredEntries.length).assertEqual(0)
    })
  })

  describe('WORKSPACE_HIDDEN_DIRS', () => {
    it('contains Hermes internal dirs', 0, () => {
      expect(WORKSPACE_HIDDEN_DIRS.has('audio_cache')).assertTrue()
      expect(WORKSPACE_HIDDEN_DIRS.has('cache')).assertTrue()
      expect(WORKSPACE_HIDDEN_DIRS.has('logs')).assertTrue()
      expect(WORKSPACE_HIDDEN_DIRS.has('sessions')).assertTrue()
      expect(WORKSPACE_HIDDEN_DIRS.has('workspace')).assertTrue()
    })

    it('does not contain user-visible dir names', 0, () => {
      expect(WORKSPACE_HIDDEN_DIRS.has('documents')).assertFalse()
      expect(WORKSPACE_HIDDEN_DIRS.has('my-folder')).assertFalse()
    })
  })
}
