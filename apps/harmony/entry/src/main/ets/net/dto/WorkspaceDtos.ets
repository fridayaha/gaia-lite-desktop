/**
 * 工作区文件 DTO。复刻 Android net/dto/WorkspaceDtos.kt。
 * Phase 0 仅占位定义；WorkspaceScreen 在 Phase 1 接入。
 */

export class WorkspaceFileEntry {
  name: string = ''
  path: string = ''
  is_dir: boolean = false
  size: number = 0
  mtime_ns: number | null = null
  is_text: boolean | null = null

  static fromJson(o: Record<string, Object>): WorkspaceFileEntry {
    const r = new WorkspaceFileEntry()
    if (typeof o['name'] === 'string') r.name = o['name']
    if (typeof o['path'] === 'string') r.path = o['path']
    if (typeof o['is_dir'] === 'boolean') r.is_dir = o['is_dir']
    if (typeof o['size'] === 'number') r.size = o['size']
    if (typeof o['mtime_ns'] === 'number') r.mtime_ns = o['mtime_ns']
    if (typeof o['is_text'] === 'boolean') r.is_text = o['is_text']
    return r
  }
}

export class WorkspaceFileList {
  entries: WorkspaceFileEntry[] = []
  path: string = ''
  error: string | null = null

  static fromJson(o: Record<string, Object>): WorkspaceFileList {
    const r = new WorkspaceFileList()
    if (typeof o['path'] === 'string') r.path = o['path']
    if (typeof o['error'] === 'string') r.error = o['error']
    const entries = o['entries']
    if (entries instanceof Array) {
      r.entries = entries.filter((v: Object) => v instanceof Object)
        .map((v: Object) => WorkspaceFileEntry.fromJson(v as Record<string, Object>))
    }
    return r
  }
}

export class WorkspaceFileContent {
  path: string = ''
  name: string = ''
  size: number = 0
  truncated: boolean = false
  is_text: boolean = false
  content: string | null = null
  content_b64: string | null = null
  is_image: boolean = false
  is_markdown: boolean = false
  max_bytes: number = 0
  error: string | null = null

  static fromJson(o: Record<string, Object>): WorkspaceFileContent {
    const r = new WorkspaceFileContent()
    if (typeof o['path'] === 'string') r.path = o['path']
    if (typeof o['name'] === 'string') r.name = o['name']
    if (typeof o['size'] === 'number') r.size = o['size']
    if (typeof o['truncated'] === 'boolean') r.truncated = o['truncated']
    if (typeof o['is_text'] === 'boolean') r.is_text = o['is_text']
    if (typeof o['content'] === 'string') r.content = o['content']
    if (typeof o['content_b64'] === 'string') r.content_b64 = o['content_b64']
    if (typeof o['is_image'] === 'boolean') r.is_image = o['is_image']
    if (typeof o['is_markdown'] === 'boolean') r.is_markdown = o['is_markdown']
    if (typeof o['max_bytes'] === 'number') r.max_bytes = o['max_bytes']
    if (typeof o['error'] === 'string') r.error = o['error']
    return r
  }
}

export class CreateFolderRequest {
  name: string = ''
}

export class MoveFileRequest {
  from_path: string = ''
  to_path: string = ''
}
