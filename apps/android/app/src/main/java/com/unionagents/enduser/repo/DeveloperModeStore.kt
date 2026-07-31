package com.unionagents.enduser.repo

import android.content.Context
import androidx.datastore.core.DataStore
import androidx.datastore.preferences.core.Preferences
import androidx.datastore.preferences.core.booleanPreferencesKey
import androidx.datastore.preferences.core.edit
import androidx.datastore.preferences.preferencesDataStore
import dagger.hilt.android.qualifiers.ApplicationContext
import kotlinx.coroutines.flow.Flow
import kotlinx.coroutines.flow.first
import kotlinx.coroutines.flow.map
import javax.inject.Inject
import javax.inject.Singleton

/**
 * 开发者模式开关（默认关闭）。
 * 关闭时：对话顶部入口叫「云盘」，工作区列表隐藏 hermes 内部黑名单目录；
 * 打开时：入口叫「工作区」，工作区列表展示全部条目。
 */
@Singleton
class DeveloperModeStore @Inject constructor(
    @ApplicationContext private val context: Context,
) {
    private val key = booleanPreferencesKey("developer_mode")

    val flow: Flow<Boolean> = context.developerModeDataStore.data.map { it[key] ?: false }

    suspend fun set(enabled: Boolean) {
        context.developerModeDataStore.edit { it[key] = enabled }
    }

    suspend fun current(): Boolean = flow.first()

    companion object {
        private val Context.developerModeDataStore: DataStore<Preferences> by preferencesDataStore("ua_developer_mode")
    }
}
