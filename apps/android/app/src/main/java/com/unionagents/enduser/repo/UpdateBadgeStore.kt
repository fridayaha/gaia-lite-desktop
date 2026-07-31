package com.unionagents.enduser.repo

import android.content.Context
import androidx.datastore.core.DataStore
import androidx.datastore.preferences.core.Preferences
import androidx.datastore.preferences.core.edit
import androidx.datastore.preferences.core.stringPreferencesKey
import androidx.datastore.preferences.preferencesDataStore
import com.unionagents.enduser.BuildConfig
import dagger.hilt.android.qualifiers.ApplicationContext
import javax.inject.Inject
import javax.inject.Singleton
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.first
import kotlinx.coroutines.launch

/**
 * 版本更新小红点状态：冷启动静默检查新版本，有更新时在「我的」tab、
 * 设置齿轮、「检查更新」行打红点；看过更新页即消点，更新的版本发布再出现。
 * latest/seen 持久化到 DataStore，红点冷启动即可显示，不等网络回来。
 */
@Singleton
class UpdateBadgeStore @Inject constructor(
    @ApplicationContext private val context: Context,
    private val appReleaseRepository: AppReleaseRepository,
) {
    private val scope = CoroutineScope(SupervisorJob() + Dispatchers.IO)

    private val latest = MutableStateFlow<String?>(null)
    private val seen = MutableStateFlow<String?>(null)

    private val _badgeVisible = MutableStateFlow(false)
    val badgeVisible: StateFlow<Boolean> = _badgeVisible

    val latestVersion: StateFlow<String?> = latest

    init {
        scope.launch {
            val prefs = context.updateStateDataStore.data.first()
            latest.value = prefs[keyLatest]
            seen.value = prefs[keySeen]
            recompute()
        }
    }

    /** 冷启动静默检查：异常吞掉，离线/弱网保持旧持久状态。 */
    fun refreshLatestAsync() {
        scope.launch {
            try {
                val release = appReleaseRepository.getLatestRelease() ?: return@launch
                val version = release.version?.takeIf { it.isNotBlank() } ?: return@launch
                latest.value = version
                context.updateStateDataStore.edit { it[keyLatest] = version }
                recompute()
            } catch (_: Exception) {
            }
        }
    }

    /** 看过更新页：消点，直到更新的版本发布。 */
    suspend fun markUpdateSeen() {
        val l = latest.value ?: return
        seen.value = l
        context.updateStateDataStore.edit { it[keySeen] = l }
        recompute()
    }

    private fun recompute() {
        _badgeVisible.value = shouldShowUpdateBadge(latest.value, BuildConfig.VERSION_NAME, seen.value)
    }

    private companion object {
        val keyLatest = stringPreferencesKey("latest_version")
        val keySeen = stringPreferencesKey("seen_version")
        val Context.updateStateDataStore: DataStore<Preferences> by preferencesDataStore("ua_update_state")
    }
}

/**
 * 红点是否显示：服务端有比当前更新的版本，且用户还没看过这一版。
 */
fun shouldShowUpdateBadge(latest: String?, current: String, seen: String?): Boolean =
    latest != null && latest != seen && VersionUtil.isVersionNewer(latest, current)
