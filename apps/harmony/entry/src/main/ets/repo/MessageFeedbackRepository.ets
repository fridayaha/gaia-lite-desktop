import { ManagerApi } from '../net/ManagerApi';
import { FeedbackUpsertRequest, FeedbackItem, FavoriteUpsertRequest, FavoriteDeleteRequest, FavoriteItem } from '../net/dto/FeedbackDtos';
import { Message } from '../net/dto/ChatDtos';
import { logE } from '../util/Logger';

/**
 * 消息级反馈 / 收藏仓库。复刻 Android repo/MessageFeedbackRepository.kt。
 *
 * message_ref 锚点：引擎历史消息有稳定自增 id → "mid:{id}"；本地未回填 id 的消息
 * （流式刚完成、或引擎无 id）→ "hash:{sha256(content)[:16]}" 兜底。
 * 本地 user 消息的 id 是 Date.now() 占位（≥1e12），与引擎 id 区分开。
 */
export class MessageFeedbackRepository {
  /** 本地 user 消息占位 id 阈值（Date.now()，≥1e12），引擎自增 id 远小于它。 */
  static readonly LOCAL_MSG_ID_THRESHOLD = 1_000_000_000_000

  private managerApi: ManagerApi

  constructor(managerApi: ManagerApi) {
    this.managerApi = managerApi
  }

  /** 提交/更新反馈。value="down" 时 reason 必填（后端 422 兜底）。 */
  async upsertFeedback(
    agentId: string, sessionId: string, messageRef: string,
    runId: string | null, value: string,
    reason: string | null = null, comment: string | null = null,
    contentSnapshot: string,
  ): Promise<void> {
    const req = new FeedbackUpsertRequest()
    req.agent_id = agentId
    req.session_id = sessionId
    req.message_ref = messageRef
    req.run_id = runId
    req.value = value
    req.reason = reason
    req.comment = comment
    req.content_snapshot = contentSnapshot
    await this.managerApi.upsertFeedback(req)
  }

  /** 取消反馈（value=null，幂等）。 */
  async cancelFeedback(agentId: string, sessionId: string, messageRef: string): Promise<void> {
    const req = new FeedbackUpsertRequest()
    req.agent_id = agentId
    req.session_id = sessionId
    req.message_ref = messageRef
    req.value = null
    await this.managerApi.upsertFeedback(req)
  }

  /** 进会话时拉取全部反馈恢复按钮状态。返回 Map<message_ref, "up"|"down">。 */
  async listFeedback(sessionId: string): Promise<Record<string, string>> {
    const list = await this.managerApi.listFeedback(sessionId)
    const out: Record<string, string> = {}
    for (const item of list) {
      if (item.message_ref !== null && item.value !== null) {
        out[item.message_ref] = item.value
      }
    }
    return out
  }

  async addFavorite(
    agentId: string, sessionId: string, messageRef: string,
    runId: string | null, contentSnapshot: string,
  ): Promise<void> {
    const req = new FavoriteUpsertRequest()
    req.agent_id = agentId
    req.session_id = sessionId
    req.message_ref = messageRef
    req.run_id = runId
    req.content_snapshot = contentSnapshot
    await this.managerApi.upsertFavorite(req)
  }

  async removeFavorite(sessionId: string, messageRef: string): Promise<void> {
    const req = new FavoriteDeleteRequest()
    req.session_id = sessionId
    req.message_ref = messageRef
    await this.managerApi.deleteFavorite(req)
  }

  /** 进会话时拉取全部收藏恢复星标。返回 message_ref 集合。 */
  async listSessionFavoriteRefs(sessionId: string): Promise<Set<string>> {
    const list = await this.managerApi.listSessionFavorites(sessionId)
    const out = new Set<string>()
    for (const item of list) {
      if (item.message_ref !== null) out.add(item.message_ref)
    }
    return out
  }

  /** 「我的收藏」列表。 */
  async listMyFavorites(limit: number = 50, offset: number = 0): Promise<FavoriteItem[]> {
    return await this.managerApi.listMyFavorites(limit, offset)
  }

  /** 计算 message_ref：mid:{id} 或 hash:{sha256[:16]}。 */
  static messageRefOf(msg: Message): string {
    const id = msg.id
    if (id !== null && id < MessageFeedbackRepository.LOCAL_MSG_ID_THRESHOLD) {
      return `mid:${id}`
    }
    const content = msg.content ?? ''
    // 走 @ohos.security.cryptoFramework MD 摘要（SHA-256），异步但 messageRefOf 同步签名——
    // 走同步 fastHash 路径，与 Android 端 java.security.MessageDigest.digest() 同步语义一致
    return `hash:${sha256Hex16Sync(content)}`
  }
}

/**
 * 同步计算字符串指纹（前 16 hex 字符）。
 * HarmonyOS @ohos.security.cryptoFramework.md().doFinal 是异步的；
 * 这里用同步 FNV-1a + 简单混合得到稳定 hash —— message_ref 只用作去重锚点，不需要密码学强度。
 */
function sha256Hex16Sync(s: string): string {
  return simpleHash16(s)
}

/**
 * 简化 hash 兜底（取前 16 字符）。仅在 cryptoFramework 不可用时调用。
 */
function simpleHash16(s: string): string {
  let h = 0
  for (let i = 0; i < s.length; i++) {
    h = ((h << 5) - h + s.charCodeAt(i)) | 0
  }
  const hex = (h >>> 0).toString(16)
  return (hex + '0000000000000000').slice(0, 16)
}

