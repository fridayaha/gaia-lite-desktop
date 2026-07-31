package com.unionagents.enduser.ui.chat

import android.net.Uri
import androidx.lifecycle.SavedStateHandle
import androidx.lifecycle.ViewModel
import androidx.lifecycle.viewModelScope
import com.unionagents.enduser.net.AgentContext
import com.unionagents.enduser.net.GatewayApi
import com.unionagents.enduser.net.dto.Attachment
import com.unionagents.enduser.net.dto.Message
import com.unionagents.enduser.net.dto.Session
import com.unionagents.enduser.net.dto.ToolCallState
import com.unionagents.enduser.repo.AgentRepository
import com.unionagents.enduser.repo.ChatRepository
import com.unionagents.enduser.repo.LastViewedStore
import com.unionagents.enduser.repo.MessageFeedbackRepository
import com.unionagents.enduser.repo.ModelRepository
import com.unionagents.enduser.repo.SpeechPlayer
import com.unionagents.enduser.repo.SpeechRepository
import com.unionagents.enduser.sse.ChatStreamRunner
import com.unionagents.enduser.sse.PendingRunStore
import com.unionagents.enduser.sse.PendingRunStore.PendingRun
import com.unionagents.enduser.sse.StreamEvent
import com.unionagents.enduser.sse.StreamProbe
import com.unionagents.enduser.sse.isReasoningEchoOfReply
import com.unionagents.enduser.ui.chat.components.ActivityEvent
import com.unionagents.enduser.ui.chat.components.filterKind
import com.unionagents.enduser.ui.chat.components.markKindDone
import com.unionagents.enduser.ui.chat.components.pushActivity
import dagger.hilt.android.lifecycle.HiltViewModel
import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.Job
import kotlinx.coroutines.async
import kotlinx.coroutines.awaitAll
import kotlinx.coroutines.delay
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.flow.first
import kotlinx.coroutines.flow.update
import kotlinx.coroutines.launch
import retrofit2.HttpException
import java.io.IOException
import javax.inject.Inject
import kotlinx.serialization.json.JsonElement
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.intOrNull
import kotlinx.serialization.json.jsonPrimitive

/** 从 usage JSON 提取总 tokens：优先 total_tokens；缺失则回退 prompt+completion。 */
private fun extractTotalTokens(usage: JsonElement?): Int? {
    val obj = usage as? JsonObject ?: return null
    obj["total_tokens"]?.jsonPrimitive?.intOrNull?.let { return it }
    val prompt = obj["prompt_tokens"]?.jsonPrimitive?.intOrNull ?: 0
    val completion = obj["completion_tokens"]?.jsonPrimitive?.intOrNull ?: 0
    return if (prompt + completion > 0) prompt + completion else null
}

@HiltViewModel
class ChatViewModel @Inject constructor(
    savedStateHandle: SavedStateHandle,
    private val agentRepository: AgentRepository,
    private val chatRepository: ChatRepository,
    private val modelRepository: ModelRepository,
    private val agentContext: AgentContext,
    private val chatStreamRunner: ChatStreamRunner,
    private val pendingRunStore: PendingRunStore,
    private val gatewayApi: GatewayApi,
    private val messageFeedbackRepository: MessageFeedbackRepository,
    private val lastViewedStore: LastViewedStore,
    private val developerModeStore: com.unionagents.enduser.repo.DeveloperModeStore,
    private val speechRepository: SpeechRepository,
    private val speechPlayer: SpeechPlayer,
) : ViewModel() {

    private val _ui = MutableStateFlow(ChatUiState())
    val ui: StateFlow<ChatUiState> = _ui.asStateFlow()

    private var streamJob: Job? = null

    // 当前流式 run 的 run_id（RunStarted 事件回填，Completed 落消息时挂到 assistant 消息上）
    private var streamingRunId: String? = null

    // 断流恢复中的 runId：submitApproval 据此在提交成功后立即清审批卡（恢复期间没有 approval.responded 事件）
    private var recoveringRunId: String? = null
    private var firstDeltaMarked = false

    /** 首个正文 delta 到达即认为"等待回复"结束（标 done + 改 label 为"已回复"） */
    private fun markFirstDeltaIfNeeded() {
        if (firstDeltaMarked) return
        firstDeltaMarked = true
        StreamProbe.mark("FIRST_DELTA")
        _ui.update {
            val connected = emitConnectedEvents(it)
            it.copy(activityEvents = markKindDone(connected, "waiting", "已回复"))
        }
    }

    /**
     * 发出"已连接 / 模型 / 等待回复"事件序列（对齐 web `addActivity('run'/'已连接', 'done')` 等）。
     * 幂等：若 activityEvents 已含 'run'/'done'（HERMES 路径 RunStarted 已发过），直接返回原列表。
     * 非 HERMES 路径无 RunStarted，由首个 delta 触发本函数发出。
     */
    private fun emitConnectedEvents(state: ChatUiState): List<ActivityEvent> {
        if (state.activityEvents.any { it.kind == "run" && it.status == "done" }) return state.activityEvents
        val withRun = pushActivity(state.activityEvents, "run", "已连接", null, "done")
        val withModel = pushActivity(withRun, "model", "模型: ${state.currentModel ?: ""}", null, "done")
        return pushActivity(withModel, "waiting", "等待回复", "已连接，等待模型输出…", "waiting")
    }

    // 历史消息附件图片字节缓存：避免 LazyColumn 滚动时反复下载。
    // key = Attachment.path，value = 已下载原始字节。
    private val attachmentImageCache = mutableMapOf<String, ByteArray>()

    init {
        val agentId = savedStateHandle.get<String>("agentId")
        if (agentId != null) {
            _ui.update { it.copy(agentId = agentId) }
            bootstrap(agentId)
        }
        viewModelScope.launch {
            developerModeStore.flow.collect { enabled ->
                _ui.update { it.copy(developerMode = enabled) }
            }
        }
        // TTS 播放状态（正在朗读的消息 ref）→ UI 高亮喇叭
        viewModelScope.launch {
            speechPlayer.speakingRef.collect { ref ->
                _ui.update { it.copy(speakingRef = ref) }
            }
        }
        speechPlayer.onUnavailable = {
            _ui.update { it.copy(toast = "设备不支持语音播放") }
        }
    }

    // 深链指定的目标会话（收藏列表跳入）；只消费一次，消费后置 null 避免后续 loadSessions 重复强制切换
    private var deeplinkSessionId: String? = savedStateHandle.get<String>("sessionId")

    private fun bootstrap(agentId: String) {
        viewModelScope.launch {
            try {
                loadAgentMeta(agentId)
                ensureRunning(agentId)
                loadSessions(autoResume = true)
                loadModels(agentId)
                // resumePendingRuns 读取 DataStore，若 DataStore 文件损坏 / IO 失败，
                // 抛出未捕获异常会冒出 viewModelScope 导致整应用崩。
                // 此处兜底：恢复失败不阻断主流程（pending run 状态可后续 GET /v1/runs/{id} 自查）。
                try {
                    resumePendingRuns(agentId)
                } catch (_: Throwable) {
                    _ui.update { it.copy(pendingRuns = emptyList()) }
                }
            } finally {
                _ui.update { it.copy(bootstrapping = false) }
            }
        }
    }

    private suspend fun loadAgentMeta(agentId: String) {
        val fallbackEngineType = _ui.value.engineType ?: agentContext.state.value.engineType
        try {
            val agents = agentRepository.getAccessibleAgents()
            val me = agents.firstOrNull { it.id == agentId }
            if (me != null) {
                _ui.update { it.copy(agentName = me.name, engineType = me.engineType) }
                agentContext.setAgent(agentId, me.engineType)
            } else {
                agentContext.setAgent(agentId, fallbackEngineType)
            }
        } catch (_: Throwable) {
            agentContext.setAgent(agentId, fallbackEngineType)
        }
    }

    private suspend fun ensureRunning(agentId: String) {
        try {
            var status = agentRepository.getDeploymentStatus(agentId)
            _ui.update { it.copy(deployStatus = status.status) }
            if (status.status == "RUNNING") return
            agentRepository.deploy(agentId)
            repeat(30) {
                delay(2000)
                status = agentRepository.getDeploymentStatus(agentId)
                _ui.update { it.copy(deployStatus = status.status) }
                if (status.status == "RUNNING") return
                if (status.status == "FAILED") {
                    _ui.update {
                        it.copy(
                            engineAvailable = false,
                            deployErrorMessage = status.errorMessage ?: "引擎启动失败",
                        )
                    }
                    return
                }
            }
            _ui.update { it.copy(engineAvailable = false, deployErrorMessage = "引擎启动超时") }
        } catch (e: Throwable) {
            _ui.update {
                it.copy(
                    engineAvailable = false,
                    deployErrorMessage = e.message ?: "引擎不可用",
                )
            }
        }
    }

    fun loadSessions(autoResume: Boolean = false) {
        val agentId = _ui.value.agentId ?: return
        viewModelScope.launch {
            _ui.update { it.copy(loadingSessions = true) }
            try {
                val list = chatRepository.listSessions().sortedByDescending { it.stableLastAt ?: it.stableCreatedAt }
                _ui.update { it.copy(sessions = list, loadingSessions = false) }
                if (autoResume && list.isNotEmpty() && _ui.value.currentSessionId == null) {
                    // 深链 sessionId（收藏列表跳入）最优先；其次「最后一次查看的会话」；兜底最新活动会话。
                    val deeplink = deeplinkSessionId?.also { deeplinkSessionId = null }
                    val target = when {
                        deeplink != null -> list.firstOrNull { it.stableId == deeplink } ?: list.first()
                        else -> {
                            val preferred = runCatching { lastViewedStore.getSession(agentId) }.getOrNull()
                            list.firstOrNull { it.stableId == preferred } ?: list.first()
                        }
                    }
                    switchSession(target.stableId)
                } else {
                    deeplinkSessionId = null
                }
            } catch (e: Throwable) {
                _ui.update { it.copy(loadingSessions = false, error = e.message ?: "加载会话失败") }
            }
        }
    }

    private fun loadModels(agentId: String) {
        viewModelScope.launch {
            try {
                val list = modelRepository.getModels(agentId)
                _ui.update {
                    it.copy(
                        models = list,
                        currentModel = it.currentModel ?: list.firstOrNull(),
                    )
                }
            } catch (_: Throwable) {
                // 模型加载失败不阻断会话
            }
        }
    }

    private suspend fun resumePendingRuns(agentId: String) {
        val valid = pendingRunStore.pruneExpired().filter { it.agent_id == agentId }
        if (valid.isEmpty()) {
            _ui.update { it.copy(pendingRuns = emptyList()) }
            return
        }
        val stillPending = mutableListOf<PendingRun>()
        var recoveredApproval: ApprovalState? = null
        for (run in valid) {
            try {
                val resp = gatewayApi.getRunStatus(run.run_id)
                when (resp.status) {
                    "completed", "failed", "cancelled" -> {
                        pendingRunStore.clearPendingRunForSession(run.session_id)
                    }
                    "waiting_for_approval" -> {
                        stillPending.add(run)
                        if (run.session_id == _ui.value.currentSessionId) {
                            recoveredApproval = ApprovalState(
                                runId = run.run_id,
                                command = "",
                                description = "等待确认是否继续执行",
                                choices = listOf("once", "session", "always", "deny"),
                                submitting = false,
                            )
                        }
                    }
                    else -> {
                        // running / queued / null：保留，等服务端自行推进或超时
                        stillPending.add(run)
                    }
                }
            } catch (_: Throwable) {
                // 状态查询失败：保守保留，不阻断 UI
                stillPending.add(run)
            }
        }
        _ui.update {
            it.copy(
                pendingRuns = stillPending,
                approvalPending = recoveredApproval ?: it.approvalPending,
            )
        }
    }

    fun newSession() {
        val agentId = _ui.value.agentId ?: return
        speechPlayer.stop()
        val engineType = _ui.value.engineType
        if (engineType?.equals("DIFY", ignoreCase = true) == true) {
            val localId = "local-${System.currentTimeMillis()}-${(1..6).map { ('a'..'z').random() }.joinToString()}"
            val s = Session(sessionId = localId, title = "未开始", createdAt = System.currentTimeMillis() / 1000.0)
            _ui.update {
                it.copy(
                    sessions = listOf(s) + it.sessions,
                    currentSessionId = localId,
                    messages = emptyList(),
                    feedback = emptyMap(),
                    favorites = emptySet(),
                    drawerOpen = false,
                )
            }
            agentContext.setSession(localId)
            viewModelScope.launch { runCatching { lastViewedStore.setSession(agentId, localId) } }
            return
        }
        viewModelScope.launch {
            try {
                val session = chatRepository.createSession(_ui.value.currentModel)
                _ui.update {
                    it.copy(
                        sessions = listOf(session) + it.sessions,
                        currentSessionId = session.stableId,
                        messages = emptyList(),
                        feedback = emptyMap(),
                        favorites = emptySet(),
                        drawerOpen = false,
                    )
                }
                agentContext.setSession(session.stableId)
                // 新建会话同样记入"最后查看"，否则冷启动/重进会回跳到新建前的旧会话
                runCatching { lastViewedStore.setSession(agentId, session.stableId) }
            } catch (e: Throwable) {
                _ui.update { it.copy(error = e.message ?: "新建会话失败") }
            }
        }
    }

    fun switchSession(sessionId: String) {
        val sid = sessionId.ifBlank { return }
        if (_ui.value.currentSessionId == sid) {
            _ui.update { it.copy(drawerOpen = false) }
            return
        }
        agentContext.setSession(sid)
        speechPlayer.stop()
        _ui.update {
            it.copy(
                currentSessionId = sid,
                drawerOpen = false,
                loadingMessages = true,
                messages = emptyList(),
                streamingContent = "",
                thinkingText = "",
                toolCalls = emptyList(),
                activityEvents = emptyList(),
                approvalPending = null,
                feedback = emptyMap(),
                favorites = emptySet(),
            )
        }
        val aid = _ui.value.agentId
        viewModelScope.launch {
            // setSession 是 fire-and-forget（不阻塞主流程），单独开个 child job 即可。
            if (aid != null) {
                launch { runCatching { lastViewedStore.setSession(aid, sid) } }
            }
            try {
                val msgs = chatRepository.listMessages(sid)
                _ui.update { it.copy(messages = msgs, loadingMessages = false) }
            } catch (e: Throwable) {
                _ui.update { it.copy(loadingMessages = false, error = e.message ?: "加载消息失败") }
            }
            // 反馈/星标状态恢复（失败静默：按钮回到未选态，不阻断会话）
            launch {
                runCatching { messageFeedbackRepository.listFeedback(sid) }
                    .onSuccess { map -> _ui.update { it.copy(feedback = map) } }
            }
            launch {
                runCatching { messageFeedbackRepository.listSessionFavoriteRefs(sid) }
                    .onSuccess { refs -> _ui.update { it.copy(favorites = refs) } }
            }
        }
    }

    fun renameSession(sessionId: String, newTitle: String) {
        val title = newTitle.trim().ifBlank { return }
        viewModelScope.launch {
            try {
                chatRepository.updateTitle(sessionId, title)
                _ui.update {
                    it.copy(sessions = it.sessions.map { s ->
                        if (s.stableId == sessionId) s.copy(title = title) else s
                    })
                }
            } catch (e: Throwable) {
                _ui.update { it.copy(error = e.message ?: "重命名失败") }
            }
        }
    }

    fun deleteSession(sessionId: String) {
        viewModelScope.launch {
            try {
                chatRepository.deleteSession(sessionId)
                val remaining = _ui.value.sessions.filter { it.stableId != sessionId }
                _ui.update {
                    val newCurrent = if (it.currentSessionId == sessionId) null else it.currentSessionId
                    it.copy(
                        sessions = remaining,
                        currentSessionId = newCurrent,
                        messages = if (newCurrent == null) emptyList() else it.messages,
                    )
                }
                if (_ui.value.currentSessionId == null) agentContext.setSession(null)
            } catch (e: Throwable) {
                _ui.update { it.copy(error = e.message ?: "删除会话失败") }
            }
        }
    }

    // ── 批量删除 ──

    fun enterMultiSelect(initialId: String) {
        _ui.update { it.copy(multiSelectMode = true, selectedSessionIds = setOf(initialId)) }
    }

    fun toggleSessionSelected(id: String) {
        _ui.update { ui ->
            val next = if (id in ui.selectedSessionIds) {
                ui.selectedSessionIds - id
            } else {
                ui.selectedSessionIds + id
            }
            // 取消选中所有 → 自动退出批量模式
            if (next.isEmpty()) ui.copy(multiSelectMode = false, selectedSessionIds = emptySet())
            else ui.copy(selectedSessionIds = next)
        }
    }

    fun selectAllSessions() {
        _ui.update { it.copy(selectedSessionIds = it.sessions.map { s -> s.stableId }.toSet()) }
    }

    fun cancelMultiSelect() {
        _ui.update { it.copy(multiSelectMode = false, selectedSessionIds = emptySet()) }
    }

    fun deleteSelectedSessions() {
        val ids = _ui.value.selectedSessionIds.toList()
        if (ids.isEmpty()) return
        viewModelScope.launch {
            var failed = 0
            // 顺序删除避免后端限流
            for (id in ids) {
                runCatching { chatRepository.deleteSession(id) }
                    .onFailure { failed++ }
            }
            val remaining = _ui.value.sessions.filter { it.stableId !in ids }
            val wasCurrentDeleted = _ui.value.currentSessionId in ids
            _ui.update { ui ->
                val newCurrent = if (wasCurrentDeleted) null else ui.currentSessionId
                ui.copy(
                    sessions = remaining,
                    currentSessionId = newCurrent,
                    messages = if (wasCurrentDeleted) emptyList() else ui.messages,
                    multiSelectMode = false,
                    selectedSessionIds = emptySet(),
                    error = if (failed > 0) "删除失败 $failed 条" else null,
                )
            }
            if (wasCurrentDeleted) agentContext.setSession(null)
        }
    }

    /**
     * 把当前会话导出到用户通过 SAF 选定的 Uri。
     * isJson=true → JSON 格式；isJson=false → Markdown 转录。
     * 写入失败（如 URI 无效）走 error 文本，不弹原生 toast。
     */
    fun writeExportToUri(uri: Uri, isJson: Boolean) {
        val state = _ui.value
        val session = state.sessions.firstOrNull { it.stableId == state.currentSessionId }
            ?: com.unionagents.enduser.net.dto.Session()
        val messages = state.messages
        viewModelScope.launch {
            try {
                val content = if (isJson) {
                    SessionExporter.toJson(session, messages)
                } else {
                    SessionExporter.toTranscript(session, messages)
                }
                chatRepository.writeTextToUri(uri, content)
                _ui.update { it.copy(toast = "已导出 ${messages.size} 条消息") }
            } catch (e: Throwable) {
                _ui.update { it.copy(toast = "导出失败：${e.message ?: "未知错误"}") }
            }
        }
    }

    fun clearToast() {
        if (_ui.value.toast != null) _ui.update { it.copy(toast = null) }
    }

    fun selectModel(model: String) {
        _ui.update { it.copy(currentModel = model, modelSheetOpen = false) }
    }

    fun openModelSheet() { _ui.update { it.copy(modelSheetOpen = true) } }
    fun closeModelSheet() { _ui.update { it.copy(modelSheetOpen = false) } }

    fun openDrawer() { _ui.update { it.copy(drawerOpen = true) } }
    fun closeDrawer() { _ui.update { it.copy(drawerOpen = false) } }

    fun clearError() { _ui.update { it.copy(error = null) } }

    /**
     * 下载历史消息中的附件原始字节（用于历史消息渲染图片缩略图）。
     * 历史消息的 Attachment 没有 localUri，只能从后端下载。
     * 已下载的字节缓存在 ViewModel 内存中，避免 LazyColumn 滚动出视野再回来重新加载。
     */
    suspend fun downloadAttachmentImage(path: String): ByteArray? {
        attachmentImageCache[path]?.let { return it }
        val agentId = _ui.value.agentId ?: return null
        return runCatching { chatRepository.downloadAttachmentBytes(agentId, path) }.getOrNull()
            ?.also { attachmentImageCache[path] = it }
    }

    fun setAgentMeta(name: String?, engineType: String?) {
        _ui.update { it.copy(agentName = name, engineType = engineType) }
        agentContext.setEngineType(engineType)
    }

    // ── 阶段 5：发送 / 停止 / 审批 ──

    fun sendMessage(text: String, attachmentUris: List<Uri> = emptyList()) {
        val agentId = _ui.value.agentId ?: return
        val model = _ui.value.currentModel ?: return
        val engineType = _ui.value.engineType
        val trimmed = text.trim()
        if (trimmed.isEmpty() && attachmentUris.isEmpty()) return
        val sessionId = _ui.value.currentSessionId
        if (sessionId == null) {
            // 隐式新建会话后再发（不阻断用户体验）
            newSession()
            viewModelScope.launch {
                _ui.first { it.currentSessionId != null }
                val sid = _ui.value.currentSessionId ?: return@launch
                startStream(agentId, sid, engineType, model, trimmed, attachmentUris)
            }
            return
        }
        startStream(agentId, sessionId, engineType, model, trimmed, attachmentUris)
    }

    private fun startStream(
        agentId: String,
        sessionId: String,
        engineType: String?,
        model: String,
        text: String,
        attachmentUris: List<Uri> = emptyList(),
    ) {
        viewModelScope.launch {
            // 1. 立即落本地 user 消息（附件用占位 Attachment：含 localUri + name，path 留空）。
            // 这样 UI 上能立即看到用户消息 + 缩略图 + 文件名，而不是等上传完成后才一起出现。
            val localAttachments = if (attachmentUris.isEmpty()) {
                emptyList()
            } else {
                attachmentUris.map { async { chatRepository.buildLocalAttachment(it) } }.awaitAll()
            }
            // content 只保留用户输入文字；附件由结构化 attachments 单独渲染（缩略图/文件名）。
            // gateway 的 attachment_hint.py 会自己把路径合进引擎可识别的 [Attached files: ...] 提示，
            // 不需要、也不应该在聊天界面显示 "Uploaded: ..."。
            val displayText = text
            val localMsgId = System.currentTimeMillis()
            _ui.update {
                it.copy(
                    messages = it.messages + Message(
                        id = localMsgId,
                        role = "user",
                        content = displayText,
                        attachments = localAttachments,
                    ),
                    isStreaming = true,
                    streamingContent = "",
                    thinkingText = "",
                    toolCalls = emptyList(),
                    activityEvents = pushActivity(emptyList(), "run", "启动智能体", "正在建立连接并发送消息…"),
                    approvalPending = null,
                    error = null,
                    retryable = false,
                )
            }

            // 2. 上传附件（后台），失败则即时提示用户并不进入流式
            val uploadedAttachments: List<Attachment> = if (attachmentUris.isEmpty()) {
                emptyList()
            } else {
                try {
                    attachmentUris.map { async { chatRepository.uploadAttachment(agentId, it) } }.awaitAll()
                } catch (e: Throwable) {
                    _ui.update {
                        it.copy(
                            error = "附件上传失败：${e.message ?: "未知错误"}",
                            retryable = false,
                        )
                    }
                    return@launch
                }
            }

            // 3. 用上传后的真实 Attachment（含 path）回填到本地 user 消息
            if (uploadedAttachments.isNotEmpty()) {
                _ui.update { state ->
                    val updatedMessages = state.messages.map { msg ->
                        if (msg.id == localMsgId) {
                            msg.copy(attachments = uploadedAttachments)
                        } else msg
                    }
                    state.copy(messages = updatedMessages)
                }
            }

            // 历史（OpenAI 风格 chat/completions）含本轮 user 消息：API 要求 messages 数组以新 prompt 收尾。
            val history = _ui.value.messages.map { it.role to (it.content ?: "") }
            // Hermes 单 run 无状态：history 必须排除本轮 user 消息（本轮 input 走 [text] 字段）。
            // 同时只取 user/assistant 可见消息，剔掉 tool / 空 assistant+tool_calls 这种引擎内部事件。
            // 对齐 apps/enduser/src/composables/useChat.ts 的 conversation_history 构造逻辑。
            val hermesHistory = _ui.value.messages
                .dropLast(1)
                .filter { it.isVisible }
                .map { com.unionagents.enduser.net.dto.HistoryItem(role = it.role, content = it.content ?: "") }

            firstDeltaMarked = false
            StreamProbe.begin("engine=$engineType model=$model input=${text.length}ch hist=${hermesHistory.size}")
            streamJob = viewModelScope.launch {
                val flow = if (engineType?.equals("HERMES", ignoreCase = true) == true) {
                    chatStreamRunner.streamHermesRun(
                        agentId = agentId,
                        sessionId = sessionId,
                        engineType = engineType,
                        model = model,
                        input = text,
                        history = hermesHistory,
                        attachments = uploadedAttachments,
                    )
                } else {
                    chatStreamRunner.streamChatCompletions(agentId, sessionId, engineType, model, history, user = null, attachments = uploadedAttachments)
                }
                val maxRetries = 2
                var attempt = 0
                while (true) {
                    try {
                        flow.collect { ev ->
                            handleStreamEvent(ev, sessionId, agentId)
                        }
                        StreamProbe.end("eof")
                        // 流自然结束：若还在 streaming，落一条 assistant 消息
                        if (_ui.value.isStreaming) {
                            val content = _ui.value.streamingContent
                            if (content.isNotBlank()) {
                                _ui.update {
                                    it.copy(
                                        messages = it.messages + Message(role = "assistant", content = content),
                                        isStreaming = false,
                                        streamingContent = "",
                                        thinkingText = "",
                                        reconnecting = false,
                                        reconnectAttempt = 0,
                                    )
                                }
                            } else {
                                _ui.update { it.copy(isStreaming = false, reconnecting = false, reconnectAttempt = 0) }
                            }
                        } else {
                            _ui.update { it.copy(reconnecting = false, reconnectAttempt = 0) }
                        }
                        break
                    } catch (e: CancellationException) {
                        StreamProbe.end("cancel")
                        throw e
                    } catch (e: Throwable) {
                        val retryable = classifySendError(e)
                        val droppedRunId = streamingRunId
                        val hasContent = _ui.value.streamingContent.isNotBlank() ||
                            _ui.value.thinkingText.isNotBlank() ||
                            _ui.value.toolCalls.isNotEmpty()
                        when {
                            // 终态：不可重试 / 重试耗尽 → 落错误消息，用户可手动重试
                            !retryable || attempt >= maxRetries -> {
                                StreamProbe.end("error ${e.javaClass.simpleName}")
                                _ui.update {
                                    it.copy(
                                        messages = it.messages + Message(
                                            role = "assistant",
                                            content = "消息发送失败：${e.message ?: "网络异常"}",
                                            providerDetails = e.message,
                                        ),
                                        isStreaming = false,
                                        streamingContent = "",
                                        thinkingText = "",
                                        reconnecting = false,
                                        reconnectAttempt = 0,
                                        error = e.message ?: "网络异常",
                                        retryable = true,
                                    )
                                }
                                viewModelScope.launch { pendingRunStore.clearPendingRunForSession(sessionId) }
                                break
                            }
                            // Hermes run 已创建：断流不重发（重发 = 引擎重复执行同一消息、
                            // 工具副作用翻倍、流式内容叠加）。run 在服务端独立推进，断开的只是
                            // 事件流——无感恢复：轮询旧 run 到终态后拉历史落结果。
                            droppedRunId != null -> {
                                recoverHermesRun(agentId, sessionId, droppedRunId, e)
                                break
                            }
                            // 非 Hermes 且已收到部分内容：重发会重复生成且流式 append 叠内容，
                            // 落部分内容 + 手动重试（对齐 web useChat 的 !hasContent 语义）
                            hasContent -> {
                                StreamProbe.end("error ${e.javaClass.simpleName}")
                                val partial = _ui.value.streamingContent
                                _ui.update {
                                    it.copy(
                                        messages = it.messages + Message(
                                            role = "assistant",
                                            content = partial.ifBlank { "连接中断，请重试" },
                                            providerDetails = e.message,
                                            liveThinking = it.thinkingText.takeIf { t -> t.isNotBlank() },
                                            liveToolCalls = it.toolCalls,
                                            liveActivityEvents = it.activityEvents,
                                        ),
                                        isStreaming = false,
                                        streamingContent = "",
                                        thinkingText = "",
                                        toolCalls = emptyList(),
                                        activityEvents = emptyList(),
                                        reconnecting = false,
                                        reconnectAttempt = 0,
                                        error = e.message ?: "网络异常",
                                        retryable = true,
                                    )
                                }
                                break
                            }
                            // 未收到任何内容：重发无副作用，指数退避自动重试（1s, 2s）
                            else -> {
                                val backoff = 1000L * (1 shl attempt)
                                attempt++
                                StreamProbe.mark("RETRY", "attempt=$attempt/$maxRetries err=${(e.message ?: e.javaClass.simpleName).take(60)}")
                                _ui.update {
                                    it.copy(
                                        reconnecting = true,
                                        reconnectAttempt = attempt,
                                        error = "重连中 ($attempt/$maxRetries)...",
                                    )
                                }
                                delay(backoff)
                            }
                        }
                    }
                }
            }
        }
    }

    /**
     * SSE/HTTP 错误分类：是否可重试。
     * - IOException / SocketTimeout → 可重试（网络抖动）
     * - HttpException 5xx / 429 → 可重试（服务端临时不可用）
     * - HttpException 401/403/4xx → 不可重试（鉴权 / 业务错误）
     * - 其他 Throwable → 不可重试（保守）
     *
     * "可重试"只代表错误性质允许恢复，具体走重发还是断流恢复由调用处按
     * run 是否已创建 / 是否已收到内容分流（重发有副作用时不得重发）。
     */
    private fun classifySendError(e: Throwable): Boolean = when (e) {
        is IOException -> true
        is HttpException -> {
            val code = e.code()
            code in 500..599 || code == 429
        }
        else -> false
    }

    /**
     * Hermes 断流无感恢复：run 在服务端独立推进，断开的只是事件流（引擎 events 端点
     * 无回放，重订阅同一会 404）。不重发 run，改为轮询 run 状态：
     * - completed → 拉会话历史落最终结果（服务端权威版本，含完整思考/工具数据）
     * - failed/cancelled → 落已流到的部分内容 + 提示
     * - waiting_for_approval → 重新浮出审批卡，用户提交后随状态翻转继续轮询
     * 全程不显示"重连中"横幅，UI 保持流式进行态——对齐 web 门户与豆包"切回来已答完"的体验。
     * 在 streamJob 内运行，stop() 取消时随 CancellationException 正常退出。
     */
    private suspend fun recoverHermesRun(agentId: String, sessionId: String, runId: String, cause: Throwable) {
        StreamProbe.mark("RECOVER", "run=$runId err=${(cause.message ?: cause.javaClass.simpleName).take(60)}")
        recoveringRunId = runId
        _ui.update { it.copy(reconnecting = false, reconnectAttempt = 0) }
        val deadline = System.currentTimeMillis() + RECOVERY_TIMEOUT_MS
        var intervalMs = 2000L
        var approvalSurfaced = false
        try {
            while (System.currentTimeMillis() < deadline) {
                val resp = runCatching { gatewayApi.getRunStatus(runId) }.getOrNull()
                when (resp?.status) {
                    "completed" -> {
                        StreamProbe.end("recovered")
                        reloadAfterRecovery(sessionId)
                        pendingRunStore.clearPendingRunForSession(sessionId)
                        return
                    }
                    "failed" -> {
                        StreamProbe.end("recover_failed")
                        landPartialAfterRecovery(sessionId, "引擎运行失败：${resp.error ?: "未知错误"}")
                        pendingRunStore.clearPendingRunForSession(sessionId)
                        return
                    }
                    "cancelled" -> {
                        StreamProbe.end("recover_cancelled")
                        landPartialAfterRecovery(sessionId, null)
                        pendingRunStore.clearPendingRunForSession(sessionId)
                        return
                    }
                    "waiting_for_approval" -> {
                        if (!approvalSurfaced) {
                            approvalSurfaced = true
                            _ui.update {
                                it.copy(
                                    approvalPending = ApprovalState(
                                        runId = runId,
                                        command = "",
                                        description = "等待确认是否继续执行",
                                        choices = listOf("once", "session", "always", "deny"),
                                        submitting = false,
                                    ),
                                )
                            }
                        }
                    }
                    else -> Unit // running / queued / 查询失败：继续等
                }
                delay(intervalMs)
                intervalMs = (intervalMs + 1000L).coerceAtMost(5000L)
            }
            StreamProbe.end("recover_timeout")
            landPartialAfterRecovery(sessionId, null)
        } finally {
            recoveringRunId = null
        }
    }

    /** 恢复成功：用服务端历史整体替换本地消息（本轮最终结果在库中），流式状态收尾。 */
    private suspend fun reloadAfterRecovery(sessionId: String) {
        val msgs = runCatching { chatRepository.listMessages(sessionId) }.getOrNull()
        if (msgs == null) {
            landPartialAfterRecovery(sessionId, null)
            return
        }
        _ui.update { state ->
            if (state.currentSessionId != sessionId) return@update state
            state.copy(
                messages = msgs,
                isStreaming = false,
                streamingContent = "",
                thinkingText = "",
                toolCalls = emptyList(),
                approvalPending = null,
                reconnecting = false,
                reconnectAttempt = 0,
            )
        }
        streamingRunId = null
        // 自动朗读对齐正常 Completed 路径（历史消息带引擎 id，反馈锚点直接用）
        val finalMsg = msgs.lastOrNull { it.role == "assistant" && !it.content.isNullOrBlank() }
        if (finalMsg != null && _ui.value.currentSessionId == sessionId && _ui.value.autoSpeak) {
            speechPlayer.speak(MessageFeedbackRepository.messageRefOf(finalMsg), finalMsg.content!!)
        }
    }

    /** 恢复未拿到最终结果：把已流到的部分内容落定，提示重新进入会话可查看完整结果。 */
    private fun landPartialAfterRecovery(sessionId: String, error: String?) {
        val partial = _ui.value.streamingContent
        _ui.update { state ->
            if (state.currentSessionId != sessionId) return@update state
            state.copy(
                messages = if (partial.isNotBlank()) {
                    state.messages + Message(
                        role = "assistant",
                        content = partial,
                        providerDetails = error,
                        liveThinking = state.thinkingText.takeIf { it.isNotBlank() },
                        liveToolCalls = state.toolCalls,
                        liveActivityEvents = state.activityEvents,
                    )
                } else {
                    state.messages
                },
                isStreaming = false,
                streamingContent = "",
                thinkingText = "",
                toolCalls = emptyList(),
                activityEvents = emptyList(),
                approvalPending = null,
                reconnecting = false,
                reconnectAttempt = 0,
                error = error ?: "网络连接中断，回复仍在后台生成，重新进入会话即可查看结果",
            )
        }
        streamingRunId = null
    }

    private fun handleStreamEvent(ev: StreamEvent, sessionId: String, agentId: String) {
        when (ev) {
            StreamEvent.Connected -> Unit
            is StreamEvent.SilenceHint -> {
                StreamProbe.mark("SILENCE_HINT", "elapsed=${ev.elapsedSeconds}s")
                // 无独立静默提示行：直接把"等待回复"事件的 detail 改为"智能体思考中（N秒）"，
                // 收起栏预览行/活动 feed 里同一行实时计数；首个 delta 到达时 markKindDone 清掉
                _ui.update {
                    if (!shouldShowSilenceHint(it.toolCalls, it.approvalPending)) return@update it
                    it.copy(
                        activityEvents = it.activityEvents.map { ae ->
                            if (ae.kind == "waiting" && ae.status == "waiting") {
                                ae.copy(detail = "智能体思考中（${ev.elapsedSeconds}秒）")
                            } else ae
                        },
                    )
                }
            }
            is StreamEvent.ContentDelta -> {
                markFirstDeltaIfNeeded()
                StreamProbe.tickVm()
                _ui.update { it.copy(streamingContent = it.streamingContent + ev.text) }
            }
            is StreamEvent.ReasoningDelta -> _ui.update { it.copy(thinkingText = it.thinkingText + ev.text) }
            is StreamEvent.HermesDelta -> {
                markFirstDeltaIfNeeded()
                StreamProbe.tickVm()
                _ui.update { it.copy(streamingContent = it.streamingContent + ev.delta) }
            }
            is StreamEvent.HermesReasoning -> {
                // reasoning.available 是每回合整段推的回复回声（非流式、无独立推理时），
                // 与累积流重叠即丢弃；多回合时它是流的后缀，startsWith 判不出来。
                val echo = isReasoningEchoOfReply(ev.text, _ui.value.streamingContent)
                StreamProbe.mark("REASONING", "len=${ev.text.length} echo=$echo")
                if (echo) return
                _ui.update { it.copy(thinkingText = it.thinkingText + ev.text) }
            }
            is StreamEvent.RunStarted -> {
                StreamProbe.mark("RUN_STARTED", ev.runId)
                streamingRunId = ev.runId // Completed 落消息时挂到消息上
                _ui.update {
                    it.copy(
                        turnStartedAt = System.currentTimeMillis(),
                        activityEvents = emitConnectedEvents(it),
                    )
                }
            }
            is StreamEvent.ToolStarted -> {
                StreamProbe.mark("TOOL_START", ev.name)
                _ui.update {
                    it.copy(
                        toolCalls = it.toolCalls + ToolCallState(ev.name, ev.preview, ev.toolCallId, completed = false, error = null),
                    )
                }
            }
            is StreamEvent.ToolCompleted -> {
                StreamProbe.mark("TOOL_DONE", "${ev.name} err=${ev.error != null}")
                _ui.update {
                    val tc = it.toolCalls.mapIndexed { _, t ->
                        if (t.name == ev.name && !t.completed) t.copy(completed = true, error = ev.error, result = ev.result)
                        else t
                    }
                    it.copy(toolCalls = tc)
                }
            }
            is StreamEvent.ApprovalRequested -> _ui.update {
                val events = filterKind(
                    pushActivity(it.activityEvents, "warning", "需审批", ev.description ?: ev.command, "waiting"),
                    "waiting",
                )
                it.copy(
                    approvalPending = ApprovalState(ev.runId, ev.command, ev.description, ev.choices, submitting = false),
                    activityEvents = events,
                )
            }
            is StreamEvent.ApprovalResponded -> {
                _ui.update {
                    it.copy(
                        approvalPending = it.approvalPending?.copy(
                            responded = true,
                            respondedChoice = ev.choice,
                            submitting = false,
                        ),
                        activityEvents = markKindDone(it.activityEvents, "warning", "已响应：${ev.choice}"),
                    )
                }
                viewModelScope.launch {
                    delay(800)
                    _ui.update { it.copy(approvalPending = null) }
                }
            }
            is StreamEvent.Completed -> {
                StreamProbe.mark("COMPLETED", "streamed=${_ui.value.streamingContent.length}ch")
                StreamProbe.end("completed")
                val content = _ui.value.streamingContent.ifBlank { ev.output ?: "" }
                // 中间过程（思考 + 工具）随消息落定，渲染在回复气泡上方（对齐 hermes TUI 顺序）
                val liveThinking = _ui.value.thinkingText.takeIf { it.isNotBlank() }
                val liveToolCalls = _ui.value.toolCalls
                val liveActivityEvents = markKindDone(_ui.value.activityEvents, "waiting", "已回复")
                // 用量元数据快照：model / 耗时 / tokens，对齐 web StatusCard
                val liveModel = _ui.value.currentModel
                val liveDurationSec = _ui.value.turnStartedAt?.let { (System.currentTimeMillis() - it) / 1000.0 }
                val liveTokens = extractTotalTokens(ev.usage)
                val newMsg = if (content.isNotBlank()) {
                    listOf(Message(
                        role = "assistant",
                        content = content,
                        runId = streamingRunId,
                        liveThinking = liveThinking,
                        liveToolCalls = liveToolCalls,
                        liveActivityEvents = liveActivityEvents,
                        liveModel = liveModel,
                        liveDurationSec = liveDurationSec,
                        liveTokens = liveTokens,
                    ))
                } else emptyList()
                streamingRunId = null
                _ui.update {
                    it.copy(
                        messages = it.messages + newMsg,
                        isStreaming = false,
                        streamingContent = "",
                        thinkingText = "",
                        toolCalls = emptyList(),
                        turnStartedAt = null,
                        activityEvents = emptyList(),
                    )
                }
                viewModelScope.launch { pendingRunStore.clearPendingRunForSession(sessionId) }
                // 自动朗读：回复完成即播放（speak 会打断上一条，符合连续对话预期）
                if (newMsg.isNotEmpty() && _ui.value.autoSpeak) {
                    speechPlayer.speak(MessageFeedbackRepository.messageRefOf(newMsg.first()), content)
                }
                // 新消息引擎 id 回填：历史响应的稳定自增 id 是反馈锚点（mid:{id}）的首选
                if (newMsg.isNotEmpty()) {
                    viewModelScope.launch { backfillMessageIds(sessionId) }
                }
            }
            is StreamEvent.Failed -> {
                StreamProbe.end("failed:${(ev.error).take(60)}")
                _ui.update {
                    val finalEvents = pushActivity(
                        markKindDone(it.activityEvents, "waiting", "已回复"),
                        "warning",
                        "运行失败",
                        ev.error,
                        "error",
                    )
                    it.copy(
                        messages = it.messages + Message(
                            role = "assistant",
                            content = "引擎运行失败：${ev.error}",
                            providerDetails = ev.error,
                            liveThinking = it.thinkingText.takeIf { t -> t.isNotBlank() },
                            liveToolCalls = it.toolCalls,
                            liveActivityEvents = finalEvents,
                        ),
                        isStreaming = false,
                        streamingContent = "",
                        thinkingText = "",
                        toolCalls = emptyList(),
                        turnStartedAt = null,
                        activityEvents = emptyList(),
                        error = ev.error,
                        retryable = true,
                    )
                }
                viewModelScope.launch { pendingRunStore.clearPendingRunForSession(sessionId) }
            }
            StreamEvent.Cancelled -> {
                StreamProbe.end("cancelled")
                _ui.update {
                    it.copy(
                        isStreaming = false,
                        streamingContent = "",
                        thinkingText = "",
                        toolCalls = emptyList(),
                        turnStartedAt = null,
                        activityEvents = emptyList(),
                    )
                }
                viewModelScope.launch { pendingRunStore.clearPendingRunForSession(sessionId) }
            }
        }
    }

    fun stop() {
        streamJob?.cancel()
        streamJob = null
        _ui.update { it.copy(isStreaming = false) }
    }

    /**
     * 清空当前会话：删旧会话 + 新建 + 列表移除旧。
     * 对齐 web useChat.clearConversation。
     */
    fun clearConversation() {
        val sid = _ui.value.currentSessionId ?: return
        val agentId = _ui.value.agentId
        viewModelScope.launch {
            if (agentId != null) {
                runCatching { chatRepository.deleteSession(sid) }
            }
            val remaining = _ui.value.sessions.filter { it.stableId != sid }
            _ui.update {
                it.copy(
                    sessions = remaining,
                    currentSessionId = null,
                    messages = emptyList(),
                    streamingContent = "",
                    thinkingText = "",
                    toolCalls = emptyList(),
                    activityEvents = emptyList(),
                    approvalPending = null,
                )
            }
            agentContext.setSession(null)
            // 立即新建一个空会话，让用户能直接开始新一轮对话
            newSession()
        }
    }

    /**
     * 重试上一条失败的消息：剥掉末尾的失败 assistant 消息 + 错误状态，取最近一条 user 消息文本重新发送。
     */
    fun retryLastMessage() {
        val state = _ui.value
        if (state.isStreaming) return
        val messages = state.messages
        // 剥末尾失败 assistant 消息
        val cleaned = if (messages.isNotEmpty() && messages.last().role == "assistant" && state.retryable) {
            messages.dropLast(1)
        } else {
            messages
        }
        val lastUser = cleaned.lastOrNull { it.role == "user" }?.content?.takeIf { it.isNotBlank() } ?: return
        _ui.update {
            it.copy(
                messages = cleaned,
                error = null,
                retryable = false,
            )
        }
        sendMessage(lastUser)
    }

    fun submitApproval(choice: String) {
        val pending = _ui.value.approvalPending ?: return
        _ui.update { it.copy(approvalPending = pending.copy(submitting = true)) }
        viewModelScope.launch {
            try {
                chatRepository.submitApproval(pending.runId, choice)
                // 正常路径：状态等 approval.responded SSE 事件回流时清；
                // 断流恢复中没有事件流：提交成功即清，恢复轮询会随状态翻转继续盯 run
                if (recoveringRunId == pending.runId) {
                    _ui.update { it.copy(approvalPending = null) }
                }
            } catch (e: Throwable) {
                _ui.update {
                    it.copy(
                        approvalPending = pending.copy(submitting = false),
                        error = e.message ?: "审批失败",
                    )
                }
            }
        }
    }

    /**
     * 点赞 / 取消赞 / 取消踩。点踩（新值）不走这里——ChatScreen 弹窗收原因后调 [submitDownFeedback]。
     * 乐观更新 + 失败回滚。
     */
    fun setFeedback(message: Message, rating: String) {
        val sid = _ui.value.currentSessionId ?: return
        val agentId = _ui.value.agentId ?: return
        val ref = MessageFeedbackRepository.messageRefOf(message)
        val current = _ui.value.feedback[ref]

        // 取消（同值再点）
        if (current == rating) {
            applyFeedback(ref, null)
            viewModelScope.launch {
                try {
                    messageFeedbackRepository.cancelFeedback(agentId, sid, ref)
                } catch (e: Throwable) {
                    applyFeedback(ref, current) // 回滚
                    _ui.update { it.copy(toast = e.message ?: "操作失败") }
                }
            }
            return
        }

        // 点赞（含踩→赞切换：直接覆盖，无需原因）
        if (rating != "up") return
        applyFeedback(ref, "up")
        viewModelScope.launch {
            try {
                messageFeedbackRepository.upsertFeedback(
                    agentId = agentId,
                    sessionId = sid,
                    messageRef = ref,
                    runId = message.runId,
                    value = "up",
                    contentSnapshot = message.content.orEmpty(),
                )
            } catch (e: Throwable) {
                applyFeedback(ref, current) // 回滚
                _ui.update { it.copy(toast = e.message ?: "操作失败") }
            }
        }
    }

    /** 点踩提交（ChatScreen 原因弹窗确定后调用）。reason 必填，comment 可选。 */
    fun submitDownFeedback(message: Message, reason: String, comment: String?) {
        val sid = _ui.value.currentSessionId ?: return
        val agentId = _ui.value.agentId ?: return
        val ref = MessageFeedbackRepository.messageRefOf(message)
        val current = _ui.value.feedback[ref]
        applyFeedback(ref, "down")
        viewModelScope.launch {
            try {
                messageFeedbackRepository.upsertFeedback(
                    agentId = agentId,
                    sessionId = sid,
                    messageRef = ref,
                    runId = message.runId,
                    value = "down",
                    reason = reason,
                    comment = comment?.ifBlank { null },
                    contentSnapshot = message.content.orEmpty(),
                )
            } catch (e: Throwable) {
                applyFeedback(ref, current) // 回滚
                _ui.update { it.copy(toast = e.message ?: "操作失败") }
            }
        }
    }

    /** 收藏/取消收藏（乐观更新 + 失败回滚）。 */
    fun toggleFavorite(message: Message) {
        val sid = _ui.value.currentSessionId ?: return
        val agentId = _ui.value.agentId ?: return
        val ref = MessageFeedbackRepository.messageRefOf(message)
        val favored = _ui.value.favorites.contains(ref)
        applyFavorite(ref, !favored)
        viewModelScope.launch {
            try {
                if (favored) {
                    messageFeedbackRepository.removeFavorite(sid, ref)
                } else {
                    messageFeedbackRepository.addFavorite(
                        agentId = agentId,
                        sessionId = sid,
                        messageRef = ref,
                        runId = message.runId,
                        contentSnapshot = message.content.orEmpty(),
                    )
                }
            } catch (e: Throwable) {
                applyFavorite(ref, favored) // 回滚
                _ui.update { it.copy(toast = e.message ?: "操作失败") }
            }
        }
    }

    private fun applyFeedback(ref: String, value: String?) {
        _ui.update {
            it.copy(
                feedback = if (value == null) it.feedback - ref else it.feedback + (ref to value),
            )
        }
    }

    private fun applyFavorite(ref: String, favored: Boolean) {
        _ui.update {
            it.copy(
                favorites = if (favored) it.favorites + ref else it.favorites - ref,
            )
        }
    }

    /**
     * 按住说话：录音文件 → gateway ASR → 识别文本（回填输入框）。
     * 失败返回 null + toast；识别为空也按失败提示（用户需重说）。
     */
    suspend fun transcribeAudio(file: java.io.File): String? {
        return try {
            val text = speechRepository.transcribe(file)
            if (text.isBlank()) {
                _ui.update { it.copy(toast = "没有识别到内容，请重试") }
                null
            } else {
                text
            }
        } catch (e: Throwable) {
            _ui.update { it.copy(toast = "语音识别失败，请重试") }
            null
        } finally {
            file.delete()
        }
    }

    /** 朗读/停止朗读某条 assistant 消息（端侧 TTS）。 */
    fun toggleSpeak(message: Message) {
        val ref = MessageFeedbackRepository.messageRefOf(message)
        speechPlayer.toggle(ref, message.content.orEmpty())
    }

    fun stopSpeak() {
        speechPlayer.stop()
    }

    /** 自动朗读开关：关闭时同时停掉当前播放。 */
    fun toggleAutoSpeak() {
        val on = !_ui.value.autoSpeak
        _ui.update { it.copy(autoSpeak = on) }
        if (!on) speechPlayer.stop()
    }

    /**
     * 流式完成后从引擎历史回填消息 id（反馈锚点 mid:{id} 的首选来源）。
     * 本地 user 消息 id 是 currentTimeMillis 占位（≥1e12），assistant 本地消息 id=null，
     * 只按 role+content 从尾部对齐填充 id=null 的 assistant 消息。失败静默（hash 兜底）。
     */
    private suspend fun backfillMessageIds(sessionId: String) {
        val history = runCatching { chatRepository.listMessages(sessionId) }.getOrNull() ?: return
        val uiMsgs = _ui.value.messages
        if (history.isEmpty() || uiMsgs.isEmpty()) return
        // 尾部对齐：历史最后一条应与 UI 最后一条 assistant 对应
        var hi = history.size - 1
        var ui = uiMsgs.size - 1
        val idByIndex = mutableMapOf<Int, Long>()
        while (hi >= 0 && ui >= 0) {
            val um = uiMsgs[ui]
            if (um.role == "assistant" && um.id == null) {
                // 在历史里从 hi 向前找内容匹配的 assistant
                var found = false
                var j = hi
                while (j >= 0) {
                    val hm = history[j]
                    if (hm.role == "assistant" && hm.content == um.content && hm.id != null) {
                        idByIndex[ui] = hm.id
                        hi = j - 1
                        found = true
                        break
                    }
                    j--
                }
                if (!found) break // 更早的本地消息与历史对不齐，停止（不动它们的 hash 锚点）
            }
            ui--
        }
        if (idByIndex.isEmpty()) return
        _ui.update { state ->
            state.copy(
                messages = state.messages.mapIndexed { idx, m ->
                    val id = idByIndex[idx]
                    if (id != null) m.copy(id = id) else m
                },
            )
        }
    }

    override fun onCleared() {
        super.onCleared()
        streamJob?.cancel()
    }

    companion object {
        // 断流恢复轮询总时长上限：覆盖分钟级 agent run；超时后落定部分内容并提示重新进入会话查看
        private const val RECOVERY_TIMEOUT_MS = 600_000L
    }
}
