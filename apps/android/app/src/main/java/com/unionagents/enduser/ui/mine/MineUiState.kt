package com.unionagents.enduser.ui.mine

import com.unionagents.enduser.net.dto.AppReleaseLatest
import com.unionagents.enduser.net.dto.FavoriteItem
import com.unionagents.enduser.net.dto.UserInfo
import com.unionagents.enduser.repo.AccountStore

data class MineUiState(
    val user: UserInfo? = null,
    val appVersion: String = "",
    val latestRelease: AppReleaseLatest? = null,
    val currentVersionRelease: AppReleaseLatest? = null,
    val checkingUpdate: Boolean = false,
    val updateAvailable: Boolean = false,
    val upToDate: Boolean = false,
    val updateError: String? = null,
    val downloadProgress: Float? = null,
    val recentFavorites: List<FavoriteItem> = emptyList(),
    val savedAccounts: List<AccountStore.SavedAccount> = emptyList(),
    val switchingAccount: Boolean = false,
    val emailChannelEnabled: Boolean = false,
    val smsChannelEnabled: Boolean = false,
    val profileSaving: Boolean = false,
    val profileError: String? = null,
    val avatarUploading: Boolean = false,
    val contactVerifying: Boolean = false,
    val presetAvatars: List<String> = emptyList(),
    val developerMode: Boolean = false,
    val updateBadge: Boolean = false, // 有新版未看：设置齿轮/检查更新行打红点
)
