package com.unionagents.enduser.ui.nav

import kotlinx.serialization.Serializable

object Routes {
    const val LOGIN = "login"
    const val AGENT_LIST = "agent_list"
    const val MINE = "mine"
    const val WORKSPACE_TAB = "workspace_tab"
    const val SETTINGS = "settings"
    const val SWITCH_ACCOUNT = "switch_account"
    const val PROFILE_EDIT = "profile_edit"
    const val ACCOUNT_SETTINGS = "account_settings"
    const val ABOUT = "about"
    const val VERSION_INFO = "version_info"
    const val UPDATE = "update"
    const val EDIT_TEXT = "edit_text"
    const val EDIT_AVATAR = "edit_avatar"
    const val VERIFY_CONTACT = "verify_contact"
    const val MINE_FAVORITES = "mine/favorites"

    @Serializable
    data class ChatRoute(val agentId: String)

    @Serializable
    data class WorkspaceRoute(val agentId: String)

    @Serializable
    data class FilePreviewRoute(val agentId: String, val path: String)
}
