package com.unionagents.enduser.repo

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
import kotlinx.serialization.Serializable
import kotlinx.serialization.builtins.ListSerializer
import kotlinx.serialization.json.Json
import javax.inject.Inject
import javax.inject.Singleton

/**
 * 最后查看会话持久化（DataStore）。
 *
 * `agentId → sessionId`：进 Agent 时默认恢复到该会话（而非最新活动会话）。
 */
@Singleton
class LastViewedStore @Inject constructor(
    @ApplicationContext private val context: Context,
    private val json: Json,
) {
    @Serializable
    data class SessionEntry(val agentId: String, val sessionId: String)

    private val sessionKey = stringPreferencesKey("sessions_json")

    private val store: DataStore<Preferences> get() = context.lastViewedDataStore

    val sessionMap: Flow<Map<String, String>> = store.data.map { prefs ->
        decodeSessions(prefs[sessionKey]).associate { it.agentId to it.sessionId }
    }

    suspend fun setSession(agentId: String, sessionId: String) {
        store.edit { prefs ->
            val current = decodeSessions(prefs[sessionKey]).toMutableList()
            val filtered = current.filterNot { it.agentId == agentId }
            val newList = filtered + SessionEntry(agentId, sessionId)
            prefs[sessionKey] = json.encodeToString(ListSerializer(SessionEntry.serializer()), newList)
        }
    }

    suspend fun getSession(agentId: String): String? =
        sessionMap.first()[agentId]

    private fun decodeSessions(raw: String?): List<SessionEntry> {
        if (raw.isNullOrBlank()) return emptyList()
        return try {
            json.decodeFromString(ListSerializer(SessionEntry.serializer()), raw)
        } catch (_: Throwable) {
            emptyList()
        }
    }

    companion object {
        private val Context.lastViewedDataStore: DataStore<Preferences> by preferencesDataStore("ua_last_viewed")
    }
}
