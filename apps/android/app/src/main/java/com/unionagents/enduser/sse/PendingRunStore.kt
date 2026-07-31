package com.unionagents.enduser.sse

import android.content.Context
import androidx.datastore.core.DataStore
import androidx.datastore.preferences.core.Preferences
import androidx.datastore.preferences.core.edit
import androidx.datastore.preferences.core.stringPreferencesKey
import androidx.datastore.preferences.preferencesDataStore
import dagger.hilt.android.qualifiers.ApplicationContext
import kotlinx.coroutines.flow.first
import kotlinx.coroutines.flow.map
import kotlinx.serialization.Serializable
import kotlinx.serialization.builtins.ListSerializer
import kotlinx.serialization.json.Json
import javax.inject.Inject
import javax.inject.Singleton

/**
 * HERMES 中断恢复 —— DataStore 持久化进行中的 run_id（5min TTL）。
 * 镜像 apps/enduser/src/composables/useChat.ts 的 PENDING_RUNS_KEY + PENDING_RUN_TTL_MS。
 *
 * 用法：
 * - ChatStreamRunner 开流前 registerPendingRun(runId, sessionId, agentId)
 * - run.completed/failed/cancelled 事件 clearPendingRun(runId)
 * - ChatViewModel 启动时 resumePendingRuns() 检查未过期 run，调 gateway GET /v1/runs/{id} 查状态，
 *   若 running/waiting_for_approval/queued 则重开 SSE 流回放事件。
 */
@Singleton
class PendingRunStore @Inject constructor(
    @ApplicationContext private val context: Context,
    private val json: Json,
) {
    @Serializable
    data class PendingRun(
        val run_id: String,
        val session_id: String,
        val agent_id: String,
        val started_at: Long, // epoch millis
    )

    private val ttlMs: Long = 5 * 60 * 1000

    private val key = stringPreferencesKey("pending_runs_json")

    private val store: DataStore<Preferences> get() = context.pendingRunsDataStore

    suspend fun registerPendingRun(runId: String, sessionId: String, agentId: String) {
        val current = readAll()
        val filtered = current.filter { System.currentTimeMillis() - it.started_at < ttlMs }
        val updated = (filtered.filter { it.run_id != runId } + PendingRun(runId, sessionId, agentId, System.currentTimeMillis()))
        writeAll(updated)
    }

    suspend fun clearPendingRun(runId: String) {
        val current = readAll()
        writeAll(current.filter { it.run_id != runId })
    }

    /**
     * 清掉指定 session 下所有 pending run（run 终结时调用——一个 session 同一时刻最多一条 active run）。
     */
    suspend fun clearPendingRunForSession(sessionId: String) {
        val current = readAll()
        writeAll(current.filter { it.session_id != sessionId })
    }

    suspend fun readAll(): List<PendingRun> {
        val raw = store.data.map { p -> p[key] ?: "" }.first()
        if (raw.isBlank()) return emptyList()
        return try {
            json.decodeFromString(ListSerializer(PendingRun.serializer()), raw)
        } catch (_: Throwable) {
            emptyList()
        }
    }

    /**
     * 清掉所有过期项（>5min），返回未过期的有效列表。
     */
    suspend fun pruneExpired(): List<PendingRun> {
        val current = readAll()
        val valid = current.filter { System.currentTimeMillis() - it.started_at < ttlMs }
        if (valid.size != current.size) writeAll(valid)
        return valid
    }

    private suspend fun writeAll(runs: List<PendingRun>) {
        store.edit { prefs ->
            if (runs.isEmpty()) {
                prefs.remove(key)
            } else {
                prefs[key] = json.encodeToString(ListSerializer(PendingRun.serializer()), runs)
            }
        }
    }

    companion object {
        private val Context.pendingRunsDataStore: DataStore<Preferences> by preferencesDataStore("ua_pending_runs")
    }
}
