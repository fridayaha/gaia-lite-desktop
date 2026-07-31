package com.unionagents.enduser.repo

import android.content.Context
import androidx.datastore.core.DataStore
import androidx.datastore.preferences.core.Preferences
import androidx.datastore.preferences.core.edit
import androidx.datastore.preferences.core.stringPreferencesKey
import androidx.datastore.preferences.preferencesDataStore
import com.unionagents.enduser.net.TokenData
import com.unionagents.enduser.net.dto.UserInfo
import dagger.hilt.android.qualifiers.ApplicationContext
import kotlinx.coroutines.flow.Flow
import kotlinx.coroutines.flow.first
import kotlinx.coroutines.flow.map
import kotlinx.serialization.Serializable
import kotlinx.serialization.builtins.ListSerializer
import kotlinx.serialization.json.Json
import javax.inject.Inject
import javax.inject.Singleton

/**
 * 多账号持久化 —— 镜像 web localStorage 的「账号列表」语义。
 * 登录成功后 saveAccount；切换账号时 listAccounts 给 UI 渲染，tap 后 restore 出 token。
 */
@Singleton
class AccountStore @Inject constructor(
    @ApplicationContext private val context: Context,
    private val json: Json,
) {
    @Serializable
    data class SavedAccount(
        val id: String,
        val username: String,
        val email: String? = null,
        val avatarUrl: String? = null,
        val accessToken: String,
        val refreshToken: String,
        val savedAt: Long,
    )

    private val key = stringPreferencesKey("saved_accounts_json")
    private val store: DataStore<Preferences> get() = context.accountStore

    val accountsFlow: Flow<List<SavedAccount>> = store.data.map { p ->
        val raw = p[key] ?: ""
        if (raw.isBlank()) emptyList()
        else runCatching {
            json.decodeFromString(ListSerializer(SavedAccount.serializer()), raw)
        }.getOrDefault(emptyList())
    }

    suspend fun listAccounts(): List<SavedAccount> = accountsFlow.first()

    suspend fun saveAccount(user: UserInfo, token: TokenData) {
        val uid = user.id ?: return
        val current = listAccounts()
        val filtered = current.filter { it.id != uid }
        val updated = filtered + SavedAccount(
            id = uid,
            username = user.username,
            email = user.email,
            avatarUrl = user.avatarUrl,
            accessToken = token.accessToken,
            refreshToken = token.refreshToken,
            savedAt = System.currentTimeMillis(),
        )
        writeAll(updated.sortedByDescending { it.savedAt })
    }

    suspend fun removeAccount(userId: String) {
        writeAll(listAccounts().filter { it.id != userId })
    }

    suspend fun getAccount(userId: String): SavedAccount? =
        listAccounts().firstOrNull { it.id == userId }

    private suspend fun writeAll(accounts: List<SavedAccount>) {
        store.edit { prefs ->
            if (accounts.isEmpty()) {
                prefs.remove(key)
            } else {
                prefs[key] = json.encodeToString(ListSerializer(SavedAccount.serializer()), accounts)
            }
        }
    }

    companion object {
        private val Context.accountStore: DataStore<Preferences> by preferencesDataStore("ua_accounts")
    }
}
