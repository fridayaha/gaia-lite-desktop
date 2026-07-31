package com.unionagents.enduser.net.dto

import kotlinx.serialization.Serializable

/** POST /v1/audio/transcriptions 响应（gateway ASR）。text 缺省兜底空串。 */
@Serializable
data class TranscribeResponse(
    val text: String? = null,
)
