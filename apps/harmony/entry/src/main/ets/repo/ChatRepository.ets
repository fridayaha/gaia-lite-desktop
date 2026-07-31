import fs from '@ohos.file.fs';
import { GatewayApi } from '../net/GatewayApi';
import { ManagerApi } from '../net/ManagerApi';
import { ApprovalRequest, Attachment, CreateSessionRequest, Message, Session, UpdateTitleRequest, UploadFileResponse } from '../net/dto/ChatDtos';
import { AttachmentHintParser } from './AttachmentHintParser';

/**
 * 聊天仓库。复刻 Android repo/ChatRepository.kt 的核心方法。
 * 会话/消息 CRUD + 审批 + 附件本地占位构建与上传。
 */
export class ChatRepository {
  private gatewayApi: GatewayApi
  private managerApi: ManagerApi

  constructor(gatewayApi: GatewayApi, managerApi: ManagerApi) {
    this.gatewayApi = gatewayApi
    this.managerApi = managerApi
  }

  async listSessions(limit: number = 50): Promise<Session[]> {
    const resp = await this.gatewayApi.listSessions(limit)
    return resp.sessions ?? resp.data ?? []
  }

  async createSession(model: string | null): Promise<Session> {
    const req = new CreateSessionRequest()
    req.model = model
    const resp = await this.gatewayApi.createSession(req)
    return resp.session ?? new Session()
  }

  async updateTitle(sessionId: string, title: string): Promise<void> {
    const req = new UpdateTitleRequest()
    req.title = title
    await this.gatewayApi.updateSession(sessionId, req)
  }

  async deleteSession(sessionId: string): Promise<void> {
    await this.gatewayApi.deleteSession(sessionId)
  }

  async listMessages(sessionId: string): Promise<Message[]> {
    const resp = await this.gatewayApi.listMessages(sessionId)
    const list = resp.messages ?? resp.data ?? []
    // 服务端入库前剥了 attachments、把路径嵌进 content 提示；还原回结构化附件。
    return list.filter((m) => m.isVisible).map((m) => AttachmentHintParser.restore(m))
  }

  async submitApproval(runId: string, choice: string): Promise<void> {
    const req = new ApprovalRequest()
    req.choice = choice
    await this.gatewayApi.submitApproval(runId, req)
  }

  /**
   * 构造本地占位 Attachment：含 localUri + name + mime + is_image，path 留空。
   * 用于消息发送时 UI 立即显示用户附件（缩略图、文件名），上传完成后再回填真实 path。
   */
  buildLocalAttachment(name: string, mime: string, localUri: string): Attachment {
    const a = new Attachment()
    a.name = name
    a.path = ''
    a.is_image = mime.startsWith('image/')
    a.mime = mime
    a.localUri = localUri
    return a
  }

  /**
   * 从后端下载附件原始字节（用于历史消息加载时渲染图片缩略图）。
   * 走 manager /agent-instances/{id}/files/download?path=...，自动带 JWT。
   */
  async downloadAttachmentBytes(agentId: string, path: string): Promise<ArrayBuffer> {
    return await this.managerApi.downloadFile(agentId, path)
  }

  /**
   * 上传单个附件到用户 profile 工作区 uploads/ 目录。
   * 返回引擎可消费的 Attachment（含 path，引擎据此读文件）。
   */
  async uploadAttachment(agentId: string, name: string, mime: string, localUri: string): Promise<Attachment> {
    const bytes = readUriBytes(localUri)
    const resp = await this.managerApi.uploadAgentFile(agentId, 'uploads', bytes, name, mime)
    const a = new Attachment()
    a.name = resp.filename ?? name
    a.path = resp.path ?? ''
    if (a.path.length === 0) throw new Error('上传失败：未返回 path')
    a.is_image = resp.is_image
    a.mime = resp.mime ?? mime
    a.size = resp.size ?? bytes.length
    a.localUri = localUri
    return a
  }
}

/** 读取 picker 返回 URI 的全部字节（picker 授权期内可读）。 */
export function readUriBytes(uri: string): Uint8Array {
  const f = fs.openSync(uri, fs.OpenMode.READ_ONLY)
  try {
    // 不依赖 stat.size：媒体库 URI 的 stat 可能返回 -1/0，直接分块读到 EOF。
    const chunk = 65536
    const buf = new ArrayBuffer(chunk)
    const parts: Uint8Array[] = []
    let total = 0
    while (true) {
      const n = fs.readSync(f.fd, buf)
      if (n <= 0) break
      parts.push(new Uint8Array(buf.slice(0, n)))
      total += n
    }
    const out = new Uint8Array(total)
    let off = 0
    for (const p of parts) {
      out.set(p, off)
      off += p.length
    }
    return out
  } finally {
    fs.closeSync(f)
  }
}
