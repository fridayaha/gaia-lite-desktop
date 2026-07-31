import { Attachment, Message } from '../net/dto/ChatDtos';

/**
 * 服务端把 messages 存到 Hermes 前会把 attachments 字段剥掉、把路径嵌进 content 作为
 * ``[Attached files: path1, path2]`` 提示（见 services/gateway/app/attachment_hint.py）。
 * 历史消息从服务端拉回时没有 attachments 字段，本工具把 hint 解析回结构化附件、
 * 并把提示文本从显示内容里剥掉，让聊天框能渲染图片缩略图。
 * 复刻 Android repo/AttachmentHintParser.kt。
 */

const HINT_REGEX = /\[Attached files:\s*([^\]]+)\]/

export namespace AttachmentHintParser {

  export function restore(msg: Message): Message {
    const content = msg.content
    if (content === null) return msg
    if (msg.attachments.length > 0) return msg
    const match = content.match(HINT_REGEX)
    if (match === null || match.length < 2) return msg
    const paths = match[1].split(',')
      .map((p: string) => p.trim())
      .filter((p: string) => p.length > 0)
    if (paths.length === 0) return msg
    msg.attachments = paths.map((p: string) => {
      const a = new Attachment()
      a.name = p.substring(p.lastIndexOf('/') + 1)
      a.path = p
      a.is_image = isImagePath(p)
      return a
    })
    msg.content = content
      .replace(match[0], '')
      .replace(/\n{2,}$/g, '')
      .trimEnd()
    return msg
  }

  export function isImagePath(path: string): boolean {
    const lower = path.toLowerCase()
    return lower.endsWith('.jpg') || lower.endsWith('.jpeg') ||
      lower.endsWith('.png') || lower.endsWith('.gif') ||
      lower.endsWith('.webp') || lower.endsWith('.bmp') ||
      lower.endsWith('.heic') || lower.endsWith('.heif') ||
      lower.endsWith('.svg')
  }
}
