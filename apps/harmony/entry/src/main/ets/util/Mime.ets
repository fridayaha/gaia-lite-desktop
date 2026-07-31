/**
 * 文件名 → MIME 推断。对齐 Android ContentResolver.getType 的常见返回。
 * picker 只给 URI 不给 mime，按扩展名映射；未知给 application/octet-stream。
 */

const MIME_MAP: Record<string, string> = {
  'jpg': 'image/jpeg',
  'jpeg': 'image/jpeg',
  'png': 'image/png',
  'gif': 'image/gif',
  'webp': 'image/webp',
  'bmp': 'image/bmp',
  'heic': 'image/heic',
  'svg': 'image/svg+xml',
  'mp4': 'video/mp4',
  'mov': 'video/quicktime',
  'mkv': 'video/x-matroska',
  'mp3': 'audio/mpeg',
  'm4a': 'audio/mp4',
  'aac': 'audio/aac',
  'wav': 'audio/wav',
  'amr': 'audio/amr',
  'flac': 'audio/flac',
  'pdf': 'application/pdf',
  'txt': 'text/plain',
  'md': 'text/markdown',
  'json': 'application/json',
  'csv': 'text/csv',
  'doc': 'application/msword',
  'docx': 'application/vnd.openxmlformats-officedocument.wordprocessingml.document',
  'xls': 'application/vnd.ms-excel',
  'xlsx': 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
  'ppt': 'application/vnd.ms-powerpoint',
  'pptx': 'application/vnd.openxmlformats-officedocument.presentationml.presentation',
  'zip': 'application/zip',
  'gz': 'application/gzip',
  'tar': 'application/x-tar',
  'log': 'text/plain',
  'xml': 'text/xml',
  'html': 'text/html',
  'js': 'text/javascript',
  'ts': 'text/typescript',
  'py': 'text/x-python',
  'java': 'text/x-java-source',
  'kt': 'text/x-kotlin',
  'go': 'text/x-go',
  'rs': 'text/x-rust',
  'c': 'text/x-c',
  'cpp': 'text/x-c++',
  'h': 'text/x-c',
  'sh': 'text/x-shellscript',
  'yaml': 'text/yaml',
  'yml': 'text/yaml',
}

export function guessMime(name: string): string {
  const idx = name.lastIndexOf('.')
  if (idx < 0 || idx === name.length - 1) return 'application/octet-stream'
  const ext = name.substring(idx + 1).toLowerCase()
  const m = MIME_MAP[ext]
  return m !== undefined ? m : 'application/octet-stream'
}

/** 从 picker 返回的 URI 提取文件名（最后一段，URL decode）。 */
export function fileNameFromUri(uri: string): string {
  const q = uri.indexOf('?')
  const clean = q >= 0 ? uri.substring(0, q) : uri
  const slash = clean.lastIndexOf('/')
  const raw = slash >= 0 ? clean.substring(slash + 1) : clean
  try {
    return decodeURIComponent(raw)
  } catch (e) {
    return raw
  }
}
