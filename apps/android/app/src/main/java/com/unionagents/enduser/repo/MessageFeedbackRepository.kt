package com.unionagents.enduser.repo

import com.unionagents.enduser.net.ManagerApi
import com.unionagents.enduser.net.dto.FavoriteDeleteRequest
import com.unionagents.enduser.net.dto.FavoriteItem
import com.unionagents.enduser.net.dto.FavoriteUpsertRequest
import com.unionagents.enduser.net.dto.FeedbackItem
import com.unionagents.enduser.net.dto.FeedbackUpsertRequest
import com.unionagents.enduser.net.dto.Message
import java.security.MessageDigest
import javax.inject.Inject
import javax.inject.Singleton

/**
 * 消息级反馈 / 收藏仓库 — manager 业务库为 source of truth（替代原 DataStore 本地 mock）。
 *
 * 锚点 message_ref：引擎历史消息有稳定自增 id → "mid:{id}"；本地未回填 id 的消息
 * （流式刚完成、或引擎无 id）→ "hash:{sha256(content)[:16]}" 兜底。
 * 本地 user 消息的 id 是 System.currentTimeMillis() 占位（≥1e12），与引擎 id 区分开。
 */
@Singleton
class MessageFeedbackRepository @Inject constructor(
    private val managerApi: ManagerApi,
) {

    /** 提交/更新反馈。value="down" 时 reason 必填（后端 422 兜底）。 */
    suspend fun upsertFeedback(
        agentId: String,
        sessionId: String,
        messageRef: String,
        runId: String?,
        value: String,
        reason: String? = null,
        comment: String? = null,
        contentSnapshot: String,
    ) {
        val resp = managerApi.upsertFeedback(
            FeedbackUpsertRequest(
                agent_id = agentId,
                session_id = sessionId,
                message_ref = messageRef,
                run_id = runId,
                value = value,
                reason = reason,
                comment = comment,
                content_snapshot = contentSnapshot,
            )
        )
        if (!resp.isSuccessful) throw FeedbackApiException(resp.code(), "提交反馈失败")
    }

    /** 取消反馈（value=null，幂等）。 */
    suspend fun cancelFeedback(agentId: String, sessionId: String, messageRef: String) {
        val resp = managerApi.upsertFeedback(
            FeedbackUpsertRequest(
                agent_id = agentId,
                session_id = sessionId,
                message_ref = messageRef,
                value = null,
            )
        )
        if (!resp.isSuccessful) throw FeedbackApiException(resp.code(), "取消反馈失败")
    }

    /** 进会话时拉取全部反馈恢复按钮状态。返回 Map<message_ref, "up"|"down">。 */
    suspend fun listFeedback(sessionId: String): Map<String, String> =
        managerApi.listFeedback(sessionId)
            .mapNotNull { it.toPair() }
            .toMap()

    suspend fun addFavorite(
        agentId: String,
        sessionId: String,
        messageRef: String,
        runId: String?,
        contentSnapshot: String,
    ) {
        managerApi.upsertFavorite(
            FavoriteUpsertRequest(
                agent_id = agentId,
                session_id = sessionId,
                message_ref = messageRef,
                run_id = runId,
                content_snapshot = contentSnapshot,
            )
        )
    }

    suspend fun removeFavorite(sessionId: String, messageRef: String) {
        val resp = managerApi.deleteFavorite(FavoriteDeleteRequest(sessionId, messageRef))
        if (!resp.isSuccessful) throw FeedbackApiException(resp.code(), "取消收藏失败")
    }

    /** 进会话时拉取全部收藏恢复星标。返回 message_ref 集合。 */
    suspend fun listSessionFavoriteRefs(sessionId: String): Set<String> =
        managerApi.listSessionFavorites(sessionId)
            .mapNotNull { it.message_ref }
            .toSet()

    /** 「我的收藏」列表。 */
    suspend fun listMyFavorites(limit: Int = 50, offset: Int = 0): List<FavoriteItem> =
        managerApi.listMyFavorites(limit, offset)

    private fun FeedbackItem.toPair(): Pair<String, String>? {
        val ref = message_ref ?: return null
        val v = value ?: return null
        return ref to v
    }

    companion object {
        /** 本地 user 消息占位 id 阈值（System.currentTimeMillis()，≥1e12），引擎自增 id 远小于它。 */
        private const val LOCAL_MSG_ID_THRESHOLD = 1_000_000_000_000L

        fun messageRefOf(msg: Message): String {
            val id = msg.id
            if (id != null && id < LOCAL_MSG_ID_THRESHOLD) return "mid:$id"
            val digest = MessageDigest.getInstance("SHA-256")
                .digest((msg.content ?: "").toByteArray(Charsets.UTF_8))
            return "hash:" + digest.joinToString("") { "%02x".format(it) }.take(16)
        }
    }
}

class FeedbackApiException(val httpCode: Int, message: String) : Exception(message)
