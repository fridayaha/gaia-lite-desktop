package com.unionagents.enduser.net.dto

import kotlinx.serialization.Serializable

/**
 * 消息级反馈 / 收藏 DTO（对齐 manager /api/manager/message-feedback(s)）。
 * 响应字段全 nullable 带默认值：后端缺字段时不崩（沿用 AppReleaseLatest 防御风格）。
 */
@Serializable
data class FeedbackUpsertRequest(
    val agent_id: String,
    val session_id: String,
    val message_ref: String,
    val run_id: String? = null,
    val value: String? = null, // "up" / "down" / null=取消
    val reason: String? = null, // down 时必选：inaccurate/harmful/off_topic/other
    val comment: String? = null,
    val content_snapshot: String = "",
)

@Serializable
data class FeedbackItem(
    val session_id: String? = null,
    val message_ref: String? = null,
    val run_id: String? = null,
    val value: String? = null,
    val reason: String? = null,
    val comment: String? = null,
)

@Serializable
data class FavoriteUpsertRequest(
    val agent_id: String,
    val session_id: String,
    val message_ref: String,
    val run_id: String? = null,
    val content_snapshot: String = "",
)

@Serializable
data class FavoriteDeleteRequest(
    val session_id: String,
    val message_ref: String,
)

@Serializable
data class FavoriteItem(
    val id: String? = null,
    val agent_id: String? = null,
    val agent_name: String? = null,
    val session_id: String? = null,
    val message_ref: String? = null,
    val content_snapshot: String? = null,
    val created_at: String? = null,
)
