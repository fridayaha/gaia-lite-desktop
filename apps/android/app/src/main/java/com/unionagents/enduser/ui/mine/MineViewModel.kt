package com.unionagents.enduser.ui.mine

import androidx.lifecycle.ViewModel
import androidx.lifecycle.viewModelScope
import com.unionagents.enduser.BuildConfig
import com.unionagents.enduser.net.AgentContext
import com.unionagents.enduser.net.ServerConfig
import com.unionagents.enduser.net.dto.AppReleaseLatest
import com.unionagents.enduser.repo.ApkDownloader
import com.unionagents.enduser.repo.AppReleaseRepository
import com.unionagents.enduser.repo.AccountStore
import com.unionagents.enduser.repo.AuthRepository
import com.unionagents.enduser.repo.DeveloperModeStore
import com.unionagents.enduser.repo.UpdateBadgeStore
import com.unionagents.enduser.repo.VersionUtil
import dagger.hilt.android.lifecycle.HiltViewModel
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.flow.update
import kotlinx.coroutines.launch
import javax.inject.Inject

@HiltViewModel
class MineViewModel @Inject constructor(
    private val authRepository: AuthRepository,
    private val agentContext: AgentContext,
    private val appReleaseRepository: AppReleaseRepository,
    private val apkDownloader: ApkDownloader,
    private val serverConfig: ServerConfig,
    private val accountStore: AccountStore,
    private val developerModeStore: DeveloperModeStore,
    private val updateBadgeStore: UpdateBadgeStore,
    private val messageFeedbackRepository: com.unionagents.enduser.repo.MessageFeedbackRepository,
) : ViewModel() {

    private val _ui = MutableStateFlow(MineUiState(appVersion = BuildConfig.VERSION_NAME))
    val ui: StateFlow<MineUiState> = _ui.asStateFlow()

    init {
        viewModelScope.launch {
            try {
                val me = authRepository.me()
                _ui.update { it.copy(user = me) }
            } catch (_: Throwable) {
                // 静默
            }
        }
        // 后台预拉一次 latest release，About 页直接用 API 返回的 display_name/version/description
        viewModelScope.launch {
            try {
                val latest = appReleaseRepository.getLatestRelease()
                _ui.update { it.copy(latestRelease = latest) }
            } catch (_: Throwable) {
                // About 页 fallback 到 BuildConfig / stringResource
            }
        }
        // 后台拉当前安装版本对应的 release（VersionInfoScreen 用其 description）
        viewModelScope.launch {
            try {
                val current = appReleaseRepository.getReleaseByVersion(BuildConfig.VERSION_NAME)
                _ui.update { it.copy(currentVersionRelease = current) }
            } catch (_: Throwable) {
                // 静默：拉不到时 VersionInfoScreen fallback 到 latest 的 description
            }
        }
        // 订阅开发者模式开关
        viewModelScope.launch {
            developerModeStore.flow.collect { enabled ->
                _ui.update { it.copy(developerMode = enabled) }
            }
        }
        // 订阅版本更新红点（冷启动静默检查由 UpdateBadgeStore 在 app 启动时完成）
        viewModelScope.launch {
            updateBadgeStore.badgeVisible.collect { visible ->
                _ui.update { it.copy(updateBadge = visible) }
            }
        }
        // 订阅本地账号列表（用于切换账号页）
        viewModelScope.launch {
            accountStore.accountsFlow.collect { accounts ->
                _ui.update { it.copy(savedAccounts = accounts) }
            }
        }
        // 拉一次系统级验证码渠道开关（决定 AccountSettings 是否显示「认证邮箱/手机」按钮）
        viewModelScope.launch {
            try {
                val channels = authRepository.getVerificationChannels()
                _ui.update {
                    it.copy(
                        emailChannelEnabled = channels.email,
                        smsChannelEnabled = channels.sms,
                    )
                }
            } catch (_: Throwable) {
                // 静默：渠道不可用时 AccountSettings 不显示认证按钮
            }
        }
        // 拉一次预置头像列表（EditAvatarScreen 用）
        viewModelScope.launch {
            try {
                val presets = authRepository.getPresetAvatars()
                _ui.update { it.copy(presetAvatars = presets) }
            } catch (_: Throwable) {
                // 静默：预置拉不到时只显示「从相册选择」
            }
        }
        loadRecentFavorites()
    }

    /** 「我的」收藏卡预览：近 10 条。失败静默（卡片显示空态）。 */
    fun loadRecentFavorites() {
        viewModelScope.launch {
            try {
                val items = messageFeedbackRepository.listMyFavorites(limit = 10)
                _ui.update { it.copy(recentFavorites = items) }
            } catch (_: Throwable) {
                // 静默
            }
        }
    }

    fun checkForUpdate() {
        if (_ui.value.checkingUpdate) return
        viewModelScope.launch {
            _ui.update { it.copy(checkingUpdate = true, updateError = null, updateAvailable = false, upToDate = false) }
            try {
                val latest = appReleaseRepository.getLatestRelease()
                _ui.update { it.copy(latestRelease = latest, checkingUpdate = false) }
                if (latest == null || latest.version.isNullOrBlank()) {
                    _ui.update { it.copy(updateError = "暂无可用的更新") }
                } else if (VersionUtil.isVersionNewer(latest.version, BuildConfig.VERSION_NAME)) {
                    _ui.update { it.copy(updateAvailable = true) }
                } else {
                    _ui.update { it.copy(upToDate = true) }
                }
            } catch (e: Throwable) {
                _ui.update {
                    it.copy(
                        checkingUpdate = false,
                        updateError = e.message ?: "检查更新失败",
                    )
                }
            }
        }
    }

    fun dismissUpdateDialog() {
        _ui.update { it.copy(updateAvailable = false, upToDate = false, updateError = null) }
    }

    /** 看过更新页：消红点，直到更新的版本发布 */
    fun markUpdateSeen() {
        viewModelScope.launch { updateBadgeStore.markUpdateSeen() }
    }

    fun clearUpdateError() {
        _ui.update { it.copy(updateError = null) }
    }

    fun setDeveloperMode(enabled: Boolean) {
        viewModelScope.launch {
            developerModeStore.set(enabled)
            // flow 订阅会自动同步到 ui.developerMode，无需手动 update
        }
    }

    fun startUpdateDownload() {
        val release = _ui.value.latestRelease ?: return
        if (release.id.isNullOrBlank() || release.version.isNullOrBlank()) return
        val url = "${serverConfig.managerUrl}public/app-releases/${release.id}/apk"
        val id = apkDownloader.startDownload(url, release.version)
        // 保持 updateAvailable=true：UpdateScreen 靠它留在「发现新版本」卡片渲染下载进度；
        // 置 false 会让 when 落入兜底分支误显「检查更新失败」（下载其实已在跑）。
        _ui.update { it.copy(downloadProgress = 0f) }
        if (id <= 0) {
            _ui.update { it.copy(downloadProgress = null) }
            return
        }
        viewModelScope.launch {
            var elapsed = 0L
            while (elapsed < 600_000L) {
                kotlinx.coroutines.delay(500L)
                elapsed += 500L
                val p = apkDownloader.queryProgress(id)
                if (p == null) {
                    _ui.update { it.copy(downloadProgress = null, updateError = "下载状态查询失败，请重试") }
                    return@launch
                }
                if (p < 0f) {
                    _ui.update { it.copy(downloadProgress = null, updateError = "下载失败，请重试") }
                    return@launch
                }
                _ui.update { it.copy(downloadProgress = p) }
                if (p >= 1f) {
                    // 安装弹窗由 ApkDownloader 的完成广播触发；进度归位后按钮恢复可重下
                    _ui.update { it.copy(downloadProgress = null) }
                    return@launch
                }
            }
            _ui.update { it.copy(downloadProgress = null) }
        }
    }

    fun logout(onDone: () -> Unit) {
        viewModelScope.launch {
            agentContext.clear()
            authRepository.logout()
            onDone()
        }
    }

    fun switchAccount(userId: String, onDone: () -> Unit) {
        if (_ui.value.switchingAccount) return
        _ui.update { it.copy(switchingAccount = true) }
        viewModelScope.launch {
            try {
                agentContext.clear()
                val me = authRepository.switchToAccount(userId)
                if (me != null) {
                    _ui.update { it.copy(user = me, switchingAccount = false) }
                    onDone()
                } else {
                    _ui.update {
                        it.copy(
                            switchingAccount = false,
                            updateError = "切换账号失败，请重新登录",
                        )
                    }
                }
            } catch (e: Throwable) {
                _ui.update {
                    it.copy(
                        switchingAccount = false,
                        updateError = e.message ?: "切换账号失败",
                    )
                }
            }
        }
    }

    fun forgetAccount(userId: String) {
        viewModelScope.launch {
            accountStore.removeAccount(userId)
        }
    }

    fun clearProfileError() {
        _ui.update { it.copy(profileError = null) }
    }

    /**
     * 改真实姓名 / 邮箱 / 手机（单字段编辑调用）。传 null 的字段不修改。
     * 改 email/phone 后 verified 回退 false，UI 会自动出现「认证」按钮（如渠道开启）。
     */
    fun updateProfile(
        realName: String? = null,
        email: String? = null,
        phone: String? = null,
    ) {
        if (_ui.value.profileSaving) return
        _ui.update { it.copy(profileSaving = true, profileError = null) }
        viewModelScope.launch {
            try {
                val me = authRepository.updateSelfProfile(
                    realName = realName,
                    email = email,
                    phone = phone,
                )
                _ui.update { it.copy(user = me, profileSaving = false) }
            } catch (e: Throwable) {
                _ui.update {
                    it.copy(
                        profileSaving = false,
                        profileError = e.message ?: "保存失败",
                    )
                }
            }
        }
    }

    /**
     * 上传新头像。file 是本地临时文件路径（由 caller 通过相机/相册 + 裁剪生成）。
     * 成功后 user.avatarUrl 更新。
     */
    fun uploadAvatar(file: java.io.File) {
        if (_ui.value.avatarUploading) return
        _ui.update { it.copy(avatarUploading = true, profileError = null) }
        viewModelScope.launch {
            try {
                val me = authRepository.uploadAvatar(file)
                _ui.update { it.copy(user = me, avatarUploading = false) }
            } catch (e: Throwable) {
                _ui.update {
                    it.copy(
                        avatarUploading = false,
                        profileError = e.message ?: "头像上传失败",
                    )
                }
            }
        }
    }

    /**
     * 选一张预置头像（不走文件上传，直接 PATCH /auth/me.avatar_url=presetUrl）。
     */
    fun selectPresetAvatar(presetUrl: String) {
        if (_ui.value.avatarUploading) return
        _ui.update { it.copy(avatarUploading = true, profileError = null) }
        viewModelScope.launch {
            try {
                val me = authRepository.updateSelfProfile(avatarUrl = presetUrl)
                _ui.update { it.copy(user = me, avatarUploading = false) }
            } catch (e: Throwable) {
                _ui.update {
                    it.copy(
                        avatarUploading = false,
                        profileError = e.message ?: "头像设置失败",
                    )
                }
            }
        }
    }

    /**
     * 改绑邮箱 — 需先 sendVerificationCode(purpose=change_email) 发码到 newEmail，
     * 用户输入 code 调本方法。
     */
    fun changeEmail(newEmail: String, code: String) {
        if (_ui.value.contactVerifying) return
        _ui.update { it.copy(contactVerifying = true, profileError = null) }
        viewModelScope.launch {
            try {
                val me = authRepository.changeEmail(newEmail, code)
                _ui.update { it.copy(user = me, contactVerifying = false) }
            } catch (e: Throwable) {
                _ui.update {
                    it.copy(
                        contactVerifying = false,
                        profileError = e.message ?: "改绑邮箱失败",
                    )
                }
            }
        }
    }

    fun changePhone(newPhone: String, code: String) {
        if (_ui.value.contactVerifying) return
        _ui.update { it.copy(contactVerifying = true, profileError = null) }
        viewModelScope.launch {
            try {
                val me = authRepository.changePhone(newPhone, code)
                _ui.update { it.copy(user = me, contactVerifying = false) }
            } catch (e: Throwable) {
                _ui.update {
                    it.copy(
                        contactVerifying = false,
                        profileError = e.message ?: "改绑手机失败",
                    )
                }
            }
        }
    }

    /**
     * 认证当前邮箱（已设邮箱但 email_verified=false）。
     * 需先 sendVerificationCode(purpose=verify_email) 发码到 user.email，
     * 用户输入 code 调本方法。
     */
    fun verifyEmail(code: String) {
        if (_ui.value.contactVerifying) return
        _ui.update { it.copy(contactVerifying = true, profileError = null) }
        viewModelScope.launch {
            try {
                val me = authRepository.verifyEmail(code)
                _ui.update { it.copy(user = me, contactVerifying = false) }
            } catch (e: Throwable) {
                _ui.update {
                    it.copy(
                        contactVerifying = false,
                        profileError = e.message ?: "认证失败",
                    )
                }
            }
        }
    }

    fun verifyPhone(code: String) {
        if (_ui.value.contactVerifying) return
        _ui.update { it.copy(contactVerifying = true, profileError = null) }
        viewModelScope.launch {
            try {
                val me = authRepository.verifyPhone(code)
                _ui.update { it.copy(user = me, contactVerifying = false) }
            } catch (e: Throwable) {
                _ui.update {
                    it.copy(
                        contactVerifying = false,
                        profileError = e.message ?: "认证失败",
                    )
                }
            }
        }
    }
}
