import { Session, Message, Attachment } from '../net/dto/ChatDtos';

/**
 * 会话导出工具：JSON / Markdown 转录。复刻 Android ui/chat/SessionExporter.kt。
 */

/** 紧凑 JSON（4 空格缩进，对齐 kotlinx prettyPrint），便于跨设备/再次导入。 */
export function exportToJson(session: Session, messages: Message[]): string {
  const msgs: Object[] = messages.map((m: Message) => {
    const o: Record<string, Object> = {
      'role': m.role,
      'content': m.content ?? '',
    }
    if (m.attachments.length > 0) {
      const atts: Object[] = m.attachments.map((att: Attachment) => {
        const a: Record<string, Object> = {
          'name': att.name,
          'path': att.path,
          'is_image': att.is_image,
        }
        if (att.mime !== null) a['mime'] = att.mime
        if (att.size !== null) a['size'] = att.size
        if (att.thumbnail_url !== null) a['thumbnail_url'] = att.thumbnail_url
        return a
      })
      o['attachments'] = atts
    }
    return o
  })
  const root: Record<string, Object> = {
    'session_id': session.stableId,
    'exported_at': Date.now(),
    'messages': msgs,
  }
  if (session.title !== null) root['title'] = session.title
  if (session.model !== null) root['model'] = session.model
  return JSON.stringify(root, null, 4)
}

/** Markdown 转录：每条消息 ## role + 内容，消息间用 --- 分隔。 */
export function exportToTranscript(session: Session, messages: Message[]): string {
  // title 可空：导出时优先用 raw title，否则用 stableId（对齐 android）
  const rawTitle = session.title ?? ''
  const title = rawTitle.length > 0 ? rawTitle
    : (session.stableId.length > 0 ? session.stableId : 'session')
  const lines: string[] = []
  lines.push(`# ${title}`)
  lines.push('')
  if (session.model !== null) {
    lines.push(`> model: ${session.model}`)
    lines.push('')
  }
  lines.push(`> exported_at: ${formatDateTime(new Date())}`)
  lines.push('')
  lines.push('---')
  lines.push('')
  for (const m of messages) {
    const role = roleLabel(m.role)
    lines.push(`## ${role}`)
    lines.push('')
    lines.push(m.content ?? '(无内容)')
    if (m.attachments.length > 0) {
      lines.push('')
      lines.push('**附件：**')
      for (const att of m.attachments) {
        const tag = att.is_image ? '图' : '文件'
        lines.push(`- [${tag}] ${att.name}`)
      }
    }
    lines.push('')
    lines.push('---')
    lines.push('')
  }
  return lines.join('\n') + '\n'
}

/** 文件名安全化：特殊字符/空白替换为 _，截断 40。 */
export function safeFilename(title: string, fallback: string): string {
  const raw = title.length > 0 ? title : fallback
  const cleaned = raw.replace(/[\\\/:*?"<>|]/g, '_').replace(/\s+/g, '_')
  const out = cleaned.slice(0, 40)
  return out.length > 0 ? out : fallback
}

function roleLabel(role: string): string {
  const r = role.toLowerCase()
  if (r === 'user') return '用户'
  if (r === 'assistant') return '助手'
  if (r === 'tool') return '工具'
  if (r === 'system') return '系统'
  return role
}

function formatDateTime(d: Date): string {
  const p = (n: number): string => n.toString().padStart(2, '0')
  return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())} ${p(d.getHours())}:${p(d.getMinutes())}:${p(d.getSeconds())}`
}
