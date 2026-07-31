package com.unionagents.enduser.net

import android.content.Context
import androidx.datastore.core.DataStore
import androidx.datastore.preferences.core.Preferences
import androidx.datastore.preferences.core.edit
import androidx.datastore.preferences.core.stringPreferencesKey
import androidx.datastore.preferences.preferencesDataStore
import dagger.hilt.android.qualifiers.ApplicationContext
import kotlinx.coroutines.flow.Flow
import kotlinx.coroutines.flow.first
import kotlinx.coroutines.flow.map
import javax.inject.Inject
import javax.inject.Singleton

private val Context.dataStore: DataStore<Preferences> by preferencesDataStore("ua_token")

@Singleton
class DataStoreTokenStorage @Inject constructor(
    @ApplicationContext private val context: Context,
) : TokenStorage {

    private val ACCESS_KEY = stringPreferencesKey("access_token")
    private val REFRESH_KEY = stringPreferencesKey("refresh_token")

    override val tokenFlow: Flow<TokenData?> = context.dataStore.data.map { prefs ->
        val access = prefs[ACCESS_KEY]
        val refresh = prefs[REFRESH_KEY]
        if (access != null && refresh != null) TokenData(access, refresh) else null
    }

    override suspend fun save(token: TokenData) {
        context.dataStore.edit { prefs ->
            prefs[ACCESS_KEY] = token.accessToken
            prefs[REFRESH_KEY] = token.refreshToken
        }
    }

    override suspend fun get(): TokenData? = tokenFlow.first()

    override suspend fun getAccessToken(): String? = get()?.accessToken

    override suspend fun getRefreshToken(): String? = get()?.refreshToken

    override suspend fun clear() {
        context.dataStore.edit { prefs ->
            prefs.remove(ACCESS_KEY)
            prefs.remove(REFRESH_KEY)
        }
    }

    // refresh() 在阶段 2 接入 AuthRepository 后实现（需要调用 /auth/refresh）
    override suspend fun refresh(): String? = null
}
