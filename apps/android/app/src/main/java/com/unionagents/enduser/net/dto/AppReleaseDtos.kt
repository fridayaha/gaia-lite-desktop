package com.unionagents.enduser.net.dto

import kotlinx.serialization.SerialName
import kotlinx.serialization.Serializable

/**
 * /api/manager/public/app-releases/latest 响应。
 * 镜像 services/manager/app/schemas/__init__.py AppReleaseLatestResponse。
 */
@Serializable
data class AppReleaseLatest(
    val id: String? = null,
    @SerialName("display_name") val displayName: String? = null,
    val description: String? = null,
    @SerialName("icon_url") val iconUrl: String? = null,
    val version: String? = null,
    val size: Long? = null,
)
