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
import javax.inject.Inject
import javax.inject.Singleton

/**
 * 持久化记录用户最后一次在对话界面选中的智能体。
 * 用于云盘/工作区 tab 在冷启动或从后台恢复时，默认切换到最近对话的智能体。
 */
@Singleton
class LastAgentStore @Inject constructor(
    @ApplicationContext private val context: Context,
) {
    private val agentIdKey = stringPreferencesKey("last_agent_id")
    private val engineTypeKey = stringPreferencesKey("last_agent_engine_type")

    private val store: DataStore<Preferences> get() = context.lastAgentDataStore

    val flow: Flow<LastAgent?> = store.data.map { prefs ->
        val agentId = prefs[agentIdKey]
        if (agentId.isNullOrBlank()) null
        else LastAgent(agentId, prefs[engineTypeKey])
    }

    suspend fun get(): LastAgent? = flow.first()

    suspend fun set(agentId: String?, engineType: String?) {
        store.edit { prefs ->
            if (agentId.isNullOrBlank()) {
                prefs.remove(agentIdKey)
                prefs.remove(engineTypeKey)
            } else {
                prefs[agentIdKey] = agentId
                if (engineType.isNullOrBlank()) {
                    prefs.remove(engineTypeKey)
                } else {
                    prefs[engineTypeKey] = engineType
                }
            }
        }
    }

    suspend fun clear() {
        set(null, null)
    }

    data class LastAgent(
        val agentId: String,
        val engineType: String?,
    )

    companion object {
        private val Context.lastAgentDataStore: DataStore<Preferences> by preferencesDataStore("ua_last_agent")
    }
}
