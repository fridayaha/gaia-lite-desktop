package com.unionagents.enduser.ui.chat

import android.content.ClipData
import android.content.ClipboardManager
import android.content.Intent
import android.net.Uri
import android.widget.Toast
import androidx.core.content.FileProvider
import java.io.File
import androidx.activity.compose.BackHandler
import androidx.activity.compose.rememberLauncherForActivityResult
import androidx.activity.result.contract.ActivityResultContracts
import androidx.compose.foundation.background
import androidx.compose.foundation.clickable
import androidx.compose.foundation.gestures.detectTapGestures
import androidx.compose.foundation.ExperimentalFoundationApi
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.BoxWithConstraints
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.widthIn
import androidx.compose.foundation.gestures.scrollBy
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.LazyListState
import androidx.compose.foundation.lazy.itemsIndexed
import androidx.compose.foundation.lazy.rememberLazyListState
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.automirrored.filled.ArrowBack
import androidx.compose.material.icons.automirrored.filled.Send
import androidx.compose.material.icons.automirrored.filled.VolumeUp
import androidx.compose.material.icons.automirrored.outlined.VolumeUp
import androidx.compose.material.icons.filled.Add
import androidx.compose.material.icons.filled.ArrowUpward
import androidx.compose.material.icons.filled.AttachFile
import androidx.compose.material.icons.filled.Code
import androidx.compose.material.icons.filled.ContentCopy
import androidx.compose.material.icons.filled.Delete
import androidx.compose.material.icons.filled.Description
import androidx.compose.material.icons.filled.Folder
import androidx.compose.material.icons.filled.Image
import androidx.compose.material.icons.filled.Menu
import androidx.compose.material.icons.filled.MoreVert
import androidx.compose.material.icons.filled.Star
import androidx.compose.material.icons.filled.Stop
import androidx.compose.material.icons.automirrored.filled.TextSnippet
import androidx.compose.material.icons.filled.ThumbDown
import androidx.compose.material.icons.filled.ThumbUp
import androidx.compose.foundation.lazy.LazyRow
import androidx.compose.material3.AlertDialog
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.OutlinedTextFieldDefaults
import androidx.compose.material3.RadioButton
import androidx.compose.material3.TextButton
import androidx.compose.material3.Card
import androidx.compose.material3.CardDefaults
import androidx.compose.material3.CircularProgressIndicator
import androidx.compose.material3.DrawerValue
import androidx.compose.material3.ExperimentalMaterial3Api
import androidx.compose.material3.HorizontalDivider
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.ModalDrawerSheet
import androidx.compose.material3.ModalNavigationDrawer
import androidx.compose.material3.Surface
import androidx.compose.material3.Text
import androidx.compose.material3.TopAppBar
import androidx.compose.material3.TopAppBarDefaults
import androidx.compose.material3.rememberDrawerState
import androidx.compose.runtime.Composable
import androidx.compose.runtime.DisposableEffect
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.produceState
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.runtime.setValue
import androidx.compose.runtime.snapshotFlow
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.vector.ImageVector
import androidx.compose.ui.layout.ContentScale
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.text.style.TextOverflow
import coil.compose.AsyncImage
import androidx.compose.ui.platform.LocalDensity
import androidx.compose.ui.res.stringResource
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.IntOffset
import androidx.compose.ui.unit.IntRect
import androidx.compose.ui.unit.IntSize
import androidx.compose.ui.unit.LayoutDirection
import androidx.compose.ui.unit.dp
import androidx.compose.ui.geometry.Offset
import androidx.compose.ui.geometry.Rect
import androidx.compose.ui.input.pointer.pointerInput
import androidx.compose.ui.layout.onGloballyPositioned
import kotlin.math.roundToInt
import androidx.compose.ui.window.Dialog
import androidx.compose.ui.window.Popup
import androidx.compose.ui.window.PopupPositionProvider
import androidx.compose.ui.window.PopupProperties
import androidx.hilt.navigation.compose.hiltViewModel
import androidx.lifecycle.compose.collectAsStateWithLifecycle
import com.unionagents.enduser.R
import com.unionagents.enduser.net.dto.Attachment
import com.unionagents.enduser.net.dto.Message
import com.unionagents.enduser.repo.MessageFeedbackRepository
import com.unionagents.enduser.ui.chat.components.ApprovalCard
import com.unionagents.enduser.ui.chat.components.IntermediateProcess
import com.unionagents.enduser.ui.chat.components.MarkdownText
import com.unionagents.enduser.ui.chat.components.StatusCard
import com.unionagents.enduser.ui.chat.components.ThinkingStatus
import com.unionagents.enduser.ui.chat.components.throttleLatest
import kotlinx.coroutines.flow.distinctUntilChanged
import kotlinx.coroutines.flow.first
import kotlinx.coroutines.flow.map
import kotlinx.coroutines.launch

@OptIn(ExperimentalMaterial3Api::class, kotlinx.coroutines.FlowPreview::class)
@Composable
fun ChatScreen(
    onBack: () -> Unit,
    onOpenWorkspace: () -> Unit,
    viewModel: ChatViewModel = hiltViewModel(),
) {
    val ui by viewModel.ui.collectAsStateWithLifecycle()
    val drawerState = rememberDrawerState(initialValue = DrawerValue.Closed)
    val listState = rememberLazyListState()
    val context = LocalContext.current
    val scope = rememberCoroutineScope()
    val clipboard = remember { context.getSystemService(ClipboardManager::class.java) }
    // 点踩原因弹窗目标消息（非空时显示弹窗）
    var downFeedbackTarget by remember { mutableStateOf<Message?>(null) }

    // 离开聊天页时停止 TTS 朗读
    DisposableEffect(Unit) {
        onDispose { viewModel.stopSpeak() }
    }

    LaunchedEffect(ui.drawerOpen) {
        if (ui.drawerOpen) drawerState.open() else drawerState.close()
    }
    LaunchedEffect(drawerState.currentValue) {
        if (drawerState.currentValue == DrawerValue.Closed && ui.drawerOpen) {
            viewModel.closeDrawer()
        }
    }
    // 新消息到来时滚到底部
    // 守 totalItemsCount > 0：messages 从 0 → N 触发 LaunchedEffect 时，LazyColumn 可能
    // 尚未完成首帧布局（layoutInfo 仍为旧值 0），animateScrollToItem(-1) 会抛 IllegalArgumentException
    // 致使整个 ChatScreen 崩溃。等 layoutInfo 反映新尺寸后再滚。
    // 用 snapshotFlow 等待 layoutInfo > 0：避免 session 切换时旧 layoutInfo 还没刷新就调用滚动的竞态。
    // 注意：streamingContent/thinkingText 不能进这里的 keys——它们每个 delta（~76/s）都变，
    // 会以同频率重启 animateScrollToItem，滚动永不停歇触发 Compose paused composition，
    // 列表项重组被无限推迟（"流式几百字后画面冻结，流结束一下子全出来"）。流式跟随见下方专用 effect。
    LaunchedEffect(
        ui.currentSessionId,
        ui.messages.size,
        ui.toolCalls.size,
        ui.approvalPending?.runId,
    ) {
        val hasContent = ui.messages.isNotEmpty() ||
            ui.streamingContent.isNotEmpty() ||
            ui.thinkingText.isNotEmpty() ||
            ui.toolCalls.isNotEmpty() ||
            ui.approvalPending != null
        if (!hasContent) return@LaunchedEffect
        // 等 LazyColumn 完成新会话的首帧布局。
        // 关键：不能只判 `it > 0`——切会话瞬间 listState.layoutInfo 可能还反映上一个会话的 item 数
        // （如从 30 条的 A 切到 6 条的 B，effect 触发时 totalItemsCount 仍可能是 30），导致后面
        // `pending.coerceAtMost(total - 1)` 用旧会话的 total 把 pending clamp 到旧会话末尾。
        // 用 `>= expected` 等到 LazyColumn 真正布局完新会话的 messages 再读 total。
        val expected = ui.messages.size
        if (expected > 0) {
            snapshotFlow { listState.layoutInfo.totalItemsCount }
                .first { it >= expected }
        }
        val target = listState.layoutInfo.totalItemsCount - 1
        if (target >= 0) {
            // 瞬时钉底，不用 animateScrollToItem：它对高过视口的最后一条消息是顶部对齐，
            // 会把视图拽到回复开头（流式落地瞬间"自动回到回复开始处"的观感）。
            listState.scrollToItem(target)
            listState.scrollBy(Float.MAX_VALUE)
        }
    }

    // 流式跟随：streamingContent/thinkingText 增长时把视图钉在底部。
    // 节流 200ms + 瞬时滚动——绝不用动画滚动跟随流式内容（动画被每个 delta
    // 重启会使 isScrollInProgress 恒真，paused composition 无限推迟气泡重组，画面冻结）。
    // 读 viewModel.ui（StateFlow）而非 snapshotFlow{ui}：本 effect 长驻，捕获的 ui 局部会变旧值。
    // 钉底必须 scrollBy(MAX)：单用 scrollToItem(target) 是把目标项顶到视口顶，气泡高过
    // 视口后新增内容落在视口外（"只滚一段就不滚了"）。
    LaunchedEffect(ui.currentSessionId) {
        viewModel.ui
            .map { it.streamingContent.length to it.thinkingText.length }
            .distinctUntilChanged()
            .throttleLatest(200)
            .collect { (contentLen, thinkingLen) ->
                if (contentLen == 0 && thinkingLen == 0) return@collect
                val target = listState.layoutInfo.totalItemsCount - 1
                if (target >= 0) {
                    listState.scrollToItem(target)
                    listState.scrollBy(Float.MAX_VALUE)
                }
            }
    }

    val onCopyMessage: (String) -> Unit = { text ->
        clipboard?.setPrimaryClip(ClipData.newPlainText("message", text))
        Toast.makeText(context, "已复制", Toast.LENGTH_SHORT).show()
    }

    LaunchedEffect(ui.toast) {
        ui.toast?.let {
            Toast.makeText(context, it, Toast.LENGTH_SHORT).show()
            viewModel.clearToast()
        }
    }

    // SAF 导出：JSON / Markdown 转录
    // rememberLauncherForActivityResult 必须在 Composable 顶层调用，不能放进回调里
    val jsonLauncher = rememberLauncherForActivityResult(
        ActivityResultContracts.CreateDocument("application/json"),
    ) { uri: Uri? ->
        if (uri != null) viewModel.writeExportToUri(uri, isJson = true)
    }
    val mdLauncher = rememberLauncherForActivityResult(
        ActivityResultContracts.CreateDocument("text/markdown"),
    ) { uri: Uri? ->
        if (uri != null) viewModel.writeExportToUri(uri, isJson = false)
    }
    val onExportJson: () -> Unit = {
        val s = ui.sessions.firstOrNull { it.stableId == ui.currentSessionId }
        val title = s?.stableTitle ?: "session"
        val name = "session-${SessionExporter.safeFilename(title, "session")}.json"
        jsonLauncher.launch(name)
    }
    val onExportTranscript: () -> Unit = {
        val s = ui.sessions.firstOrNull { it.stableId == ui.currentSessionId }
        val title = s?.stableTitle ?: "session"
        val name = "transcript-${SessionExporter.safeFilename(title, "session")}.md"
        mdLauncher.launch(name)
    }
    val onJumpToQuestion: (Int) -> Unit = { assistantIdx ->
        val msgs = ui.messages
        scope.launch {
            for (i in assistantIdx - 1 downTo 0) {
                if (msgs[i].role == "user") {
                    listState.animateScrollToItem(i)
                    return@launch
                }
            }
        }
    }

    ModalNavigationDrawer(
        drawerState = drawerState,
        drawerContent = {
            ModalDrawerSheet(
                modifier = Modifier.fillMaxWidth(0.85f),
            ) {
                SessionDrawer(
                    sessions = ui.sessions,
                    currentSessionId = ui.currentSessionId,
                    loading = ui.loadingSessions,
                    multiSelectMode = ui.multiSelectMode,
                    selectedSessionIds = ui.selectedSessionIds,
                    onSelect = viewModel::switchSession,
                    onNew = viewModel::newSession,
                    onRename = viewModel::renameSession,
                    onDelete = viewModel::deleteSession,
                    onClose = viewModel::closeDrawer,
                    onEnterMultiSelect = viewModel::enterMultiSelect,
                    onToggleSelected = viewModel::toggleSessionSelected,
                    onSelectAll = viewModel::selectAllSessions,
                    onCancelMultiSelect = viewModel::cancelMultiSelect,
                    onDeleteSelected = viewModel::deleteSelectedSessions,
                )
            }
        },
    ) {
        // 不开 edge-to-edge（见 MainActivity），DecorView 已 fit system windows：
        // status bar / nav bar / IME 全由 framework 处理，无需任何 *Padding()。
        // IME 弹出时 framework resize window → ComposeView 缩小 → messages 容器 (weight=1f)
        // 压缩 → 用 onSizeChanged 监听压缩并滚到底（见下方 Box）。
        Box(
            modifier = Modifier
                .fillMaxSize()
                .background(MaterialTheme.colorScheme.background),
        ) {
            Column(
                modifier = Modifier.fillMaxSize().background(MaterialTheme.colorScheme.background),
            ) {
            ChatTopBar(
                title = ui.agentName ?: "智能体",
                onBack = onBack,
                onOpenDrawer = viewModel::openDrawer,
                onOpenWorkspace = onOpenWorkspace,
                workspaceLabel = if (ui.developerMode) "工作区" else "云盘",
                onClearConversation = viewModel::clearConversation,
                onExportJson = onExportJson,
                onExportTranscript = onExportTranscript,
                hasSession = ui.currentSessionId != null,
                autoSpeak = ui.autoSpeak,
                onToggleAutoSpeak = viewModel::toggleAutoSpeak,
            )
            // messages 容器：weight=1f 让它在 IME 弹出时被压缩。onSizeChanged 捕获压缩事件滚到底，
            // 避免 adjustResize 后 LazyColumn 保留旧 scroll 位置、最新消息被 Composer 遮住。
            // 阈值 80px 防抖动：gesture 导航条显示/隐藏等引起的小幅高度变化不触发滚动。
            // 仅压缩前已贴底才钉底：用户翻历史时弹出 IME 不应改变查看位置。
            var prevMessagesHeight by remember { mutableStateOf(0) }
            var wasAtBottom by remember { mutableStateOf(true) }
            LaunchedEffect(listState) {
                snapshotFlow {
                    val info = listState.layoutInfo
                    val last = info.visibleItemsInfo.lastOrNull()
                    // 严格贴底：最后一条必须完整可见（底边不超出内容区末尾，4px 容差防舍入）。
                    // 只看 last.index 会把「正在看最后一条长回复的中段」误判为贴底——
                    // 长回复中段时最后一条仍是最后一个可见项，IME 弹起就触发钉底，
                    // scrollToItem 对高过视口的项是顶对齐 → 视图跳到该回复开头。
                    last != null && last.index == info.totalItemsCount - 1 &&
                        last.offset + last.size <= info.viewportEndOffset - info.afterContentPadding + 4
                }.collect { wasAtBottom = it }
            }
            Box(
                modifier = Modifier
                    .weight(1f)
                    .fillMaxWidth()
                    .onGloballyPositioned { coords ->
                        val h = coords.size.height
                        val shrunk = prevMessagesHeight > 0 && h < prevMessagesHeight - 80
                        prevMessagesHeight = h
                        if (shrunk && ui.messages.isNotEmpty() && wasAtBottom) {
                            scope.launch {
                                // 等 LazyColumn 在新约束下完成布局
                                snapshotFlow { listState.layoutInfo.totalItemsCount }
                                    .first { it >= ui.messages.size }
                                listState.scrollToBottom()
                            }
                        }
                    },
            ) {
                when {
                    !ui.engineAvailable -> EngineUnavailable(message = ui.deployErrorMessage)
                    ui.bootstrapping -> LoadingMessages()
                    ui.currentSessionId == null -> NoSessionSelected(onNew = viewModel::newSession)
                    ui.loadingMessages -> LoadingMessages()
                    else -> ChatContent(
                        state = ui,
                        onRetry = { viewModel.sendMessage(it) },
                        onRetryLast = viewModel::retryLastMessage,
                        onApproval = viewModel::submitApproval,
                        onCopy = onCopyMessage,
                        onFeedback = { msg, rating ->
                            // 点踩（新值）先弹原因表单；取消踩/赞/取消赞直接走 ViewModel
                            val ref = MessageFeedbackRepository.messageRefOf(msg)
                            if (rating == "down" && ui.feedback[ref] != "down") {
                                downFeedbackTarget = msg
                            } else {
                                viewModel.setFeedback(msg, rating)
                            }
                        },
                        onToggleFavorite = { msg -> viewModel.toggleFavorite(msg) },
                        onToggleSpeak = { msg -> viewModel.toggleSpeak(msg) },
                        onJumpToQuestion = onJumpToQuestion,
                        onClearConversation = viewModel::clearConversation,
                        onFetchAttachmentBytes = { path ->
                            viewModel.downloadAttachmentImage(path)
                        },
                        onViewAttachment = { attachment ->
                            viewAttachment(context, attachment, viewModel, scope)
                        },
                        listState = listState,
                        modifier = Modifier.fillMaxSize(),
                    )
                }
            }
            if (ui.reconnecting) {
                ReconnectingBanner(attempt = ui.reconnectAttempt)
            }
            Composer(
                enabled = ui.engineAvailable && ui.currentSessionId != null && !ui.isStreaming,
                currentModel = ui.currentModel,
                onOpenModelSheet = viewModel::openModelSheet,
                onSend = viewModel::sendMessage,
                isStreaming = ui.isStreaming,
                onStop = viewModel::stop,
                onTranscribe = { file -> viewModel.transcribeAudio(file) },
            )
            if (ui.modelSheetOpen) {
                ModelSheet(
                    models = ui.models,
                    currentModel = ui.currentModel,
                    onSelect = viewModel::selectModel,
                    onDismiss = viewModel::closeModelSheet,
                )
            }
            downFeedbackTarget?.let { target ->
                DownFeedbackDialog(
                    onSubmit = { reason, comment ->
                        viewModel.submitDownFeedback(target, reason, comment)
                        downFeedbackTarget = null
                    },
                    onDismiss = { downFeedbackTarget = null },
                )
            }
            }
        }
    }
}

/**
 * 点踩原因表单：必选分类 + 可选补充文本（对齐 ChatGPT 点踩交互）。
 * 视觉风格沿用 SessionDrawer 的 Rename/DeleteDialog：Dialog + Surface(20dp) + 图标头 + 填充主按钮。
 */
@Composable
private fun DownFeedbackDialog(
    onSubmit: (reason: String, comment: String?) -> Unit,
    onDismiss: () -> Unit,
) {
    val reasons = listOf(
        "inaccurate" to "不准确",
        "harmful" to "有害或不当",
        "off_topic" to "跑题未解决",
        "other" to "其他",
    )
    var selected by remember { mutableStateOf<String?>(null) }
    var comment by remember { mutableStateOf("") }

    Dialog(onDismissRequest = onDismiss) {
        Surface(
            shape = RoundedCornerShape(20.dp),
            color = MaterialTheme.colorScheme.surface,
            tonalElevation = 6.dp,
            modifier = Modifier.fillMaxWidth(),
        ) {
            Column(modifier = Modifier.padding(20.dp)) {
                Row(verticalAlignment = Alignment.CenterVertically) {
                    Surface(
                        shape = RoundedCornerShape(10.dp),
                        color = MaterialTheme.colorScheme.errorContainer,
                        modifier = Modifier.size(32.dp),
                    ) {
                        Box(contentAlignment = Alignment.Center) {
                            Icon(
                                Icons.Filled.ThumbDown,
                                contentDescription = null,
                                tint = MaterialTheme.colorScheme.onErrorContainer,
                                modifier = Modifier.size(18.dp),
                            )
                        }
                    }
                    Spacer(Modifier.size(12.dp))
                    Text(
                        text = "反馈问题",
                        style = MaterialTheme.typography.titleMedium,
                        fontWeight = FontWeight.SemiBold,
                    )
                }
                Spacer(Modifier.size(16.dp))
                reasons.forEach { (value, label) ->
                    val isSelected = selected == value
                    Surface(
                        onClick = { selected = value },
                        shape = RoundedCornerShape(12.dp),
                        color = if (isSelected) MaterialTheme.colorScheme.secondaryContainer
                        else Color.Transparent,
                        modifier = Modifier.fillMaxWidth(),
                    ) {
                        Row(
                            modifier = Modifier.padding(horizontal = 8.dp, vertical = 4.dp),
                            verticalAlignment = Alignment.CenterVertically,
                        ) {
                            RadioButton(selected = isSelected, onClick = { selected = value })
                            Text(text = label, style = MaterialTheme.typography.bodyMedium)
                        }
                    }
                }
                Spacer(Modifier.size(8.dp))
                OutlinedTextField(
                    value = comment,
                    onValueChange = { comment = it },
                    modifier = Modifier.fillMaxWidth(),
                    placeholder = { Text("补充说明（可选）") },
                    shape = RoundedCornerShape(12.dp),
                    textStyle = MaterialTheme.typography.bodyMedium,
                    maxLines = 3,
                )
                Spacer(Modifier.size(20.dp))
                Row(
                    modifier = Modifier.fillMaxWidth(),
                    horizontalArrangement = Arrangement.End,
                    verticalAlignment = Alignment.CenterVertically,
                ) {
                    TextButton(onClick = onDismiss) { Text("取消") }
                    Spacer(Modifier.size(8.dp))
                    val submitEnabled = selected != null
                    Surface(
                        onClick = { if (submitEnabled) selected?.let { onSubmit(it, comment.ifBlank { null }) } },
                        shape = RoundedCornerShape(10.dp),
                        color = if (submitEnabled) MaterialTheme.colorScheme.primary
                        else MaterialTheme.colorScheme.surfaceVariant,
                    ) {
                        Text(
                            text = "提交",
                            color = if (submitEnabled) MaterialTheme.colorScheme.onPrimary
                            else MaterialTheme.colorScheme.onSurfaceVariant,
                            style = MaterialTheme.typography.labelLarge,
                            fontWeight = FontWeight.Medium,
                            modifier = Modifier.padding(horizontal = 16.dp, vertical = 8.dp),
                        )
                    }
                }
            }
        }
    }
}

@OptIn(ExperimentalMaterial3Api::class)
@Composable
private fun ChatTopBar(
    title: String,
    onBack: () -> Unit,
    onOpenDrawer: () -> Unit,
    onOpenWorkspace: () -> Unit,
    workspaceLabel: String,
    onClearConversation: () -> Unit,
    onExportJson: () -> Unit,
    onExportTranscript: () -> Unit,
    hasSession: Boolean,
    autoSpeak: Boolean,
    onToggleAutoSpeak: () -> Unit,
) {
    var showOverflow by remember { mutableStateOf(false) }
    var showClearConfirm by remember { mutableStateOf(false) }
    TopAppBar(
        title = {
            Text(
                text = title,
                fontWeight = FontWeight.SemiBold,
                style = MaterialTheme.typography.titleMedium,
            )
        },
        navigationIcon = {
            IconButton(onClick = onBack, modifier = Modifier.size(48.dp)) {
                Icon(Icons.AutoMirrored.Filled.ArrowBack, contentDescription = "返回")
            }
        },
        actions = {
            // 自动朗读开关：开=每条回复完成自动 TTS 播放；关=停止当前播放
            IconButton(onClick = onToggleAutoSpeak, modifier = Modifier.size(48.dp)) {
                Icon(
                    if (autoSpeak) Icons.AutoMirrored.Filled.VolumeUp else Icons.AutoMirrored.Outlined.VolumeUp,
                    contentDescription = if (autoSpeak) "关闭自动朗读" else "开启自动朗读",
                    tint = if (autoSpeak) MaterialTheme.colorScheme.primary else MaterialTheme.colorScheme.onSurfaceVariant,
                )
            }
            IconButton(onClick = onOpenDrawer, modifier = Modifier.size(48.dp)) {
                Icon(Icons.Filled.Menu, contentDescription = "会话列表")
            }
            IconButton(onClick = onOpenWorkspace, modifier = Modifier.size(48.dp)) {
                Icon(Icons.Filled.Folder, contentDescription = workspaceLabel)
            }
            if (hasSession) {
                Box {
                    IconButton(
                        onClick = { showOverflow = true },
                        modifier = Modifier.size(48.dp),
                    ) {
                        Icon(Icons.Filled.MoreVert, contentDescription = "更多")
                    }
                    if (showOverflow) {
                        val density = LocalDensity.current
                        val marginPx = with(density) { 4.dp.roundToPx() }
                        Popup(
                            popupPositionProvider = AnchorBottomEndPopupPositionProvider(
                                marginPx = marginPx,
                            ),
                            onDismissRequest = { showOverflow = false },
                            properties = PopupProperties(focusable = true, clippingEnabled = false),
                        ) {
                            Card(
                                shape = RoundedCornerShape(20.dp),
                                colors = CardDefaults.cardColors(
                                    containerColor = MaterialTheme.colorScheme.surface,
                                ),
                                elevation = CardDefaults.cardElevation(defaultElevation = 8.dp),
                                modifier = Modifier.width(220.dp),
                            ) {
                                Column(modifier = Modifier.padding(vertical = 4.dp)) {
                                    OverflowMenuItem(
                                        icon = Icons.Filled.Code,
                                        label = "导出 JSON",
                                        onClick = {
                                            showOverflow = false
                                            onExportJson()
                                        },
                                    )
                                    OverflowMenuItem(
                                        icon = Icons.Filled.Description,
                                        label = "下载转录 Markdown",
                                        onClick = {
                                            showOverflow = false
                                            onExportTranscript()
                                        },
                                    )
                                    HorizontalDivider(
                                        color = MaterialTheme.colorScheme.outlineVariant,
                                    )
                                    OverflowMenuItem(
                                        icon = Icons.Filled.Delete,
                                        label = "清空对话",
                                        tint = MaterialTheme.colorScheme.error,
                                        onClick = {
                                            showOverflow = false
                                            showClearConfirm = true
                                        },
                                    )
                                }
                            }
                        }
                    }
                }
            }
        },
        colors = TopAppBarDefaults.topAppBarColors(
            containerColor = MaterialTheme.colorScheme.background,
            titleContentColor = MaterialTheme.colorScheme.onBackground,
            navigationIconContentColor = MaterialTheme.colorScheme.onBackground,
            actionIconContentColor = MaterialTheme.colorScheme.onBackground,
        ),
    )
    if (showClearConfirm) {
        AlertDialog(
            onDismissRequest = { showClearConfirm = false },
            title = { Text("清空当前对话？", style = MaterialTheme.typography.titleMedium) },
            text = { Text("将删除当前会话并新建一个空会话，历史消息不可恢复。", style = MaterialTheme.typography.bodyMedium) },
            confirmButton = {
                TextButton(onClick = {
                    showClearConfirm = false
                    onClearConversation()
                }) { Text("清空", color = MaterialTheme.colorScheme.error) }
            },
            dismissButton = {
                TextButton(onClick = { showClearConfirm = false }) { Text("取消") }
            },
        )
    }
}

@Composable
private fun NoSessionSelected(onNew: () -> Unit) {
    Surface(
        onClick = onNew,
        modifier = Modifier.fillMaxSize(),
        color = Color.Transparent,
    ) {
        Column(
            modifier = Modifier.fillMaxSize().padding(24.dp),
            horizontalAlignment = Alignment.CenterHorizontally,
            verticalArrangement = Arrangement.Center,
        ) {
            Icon(
                Icons.Filled.Add,
                contentDescription = stringResource(R.string.session_new),
                tint = MaterialTheme.colorScheme.primary,
                modifier = Modifier.size(56.dp),
            )
            Spacer(Modifier.height(12.dp))
            Text(
                text = "新建会话开始对话",
                style = MaterialTheme.typography.bodyMedium,
                color = MaterialTheme.colorScheme.onSurfaceVariant,
            )
        }
    }
}

@Composable
private fun LoadingMessages() {
    Box(modifier = Modifier.fillMaxSize(), contentAlignment = Alignment.Center) {
        CircularProgressIndicator(modifier = Modifier.size(28.dp), strokeWidth = 2.dp)
    }
}

@Composable
private fun EngineUnavailable(message: String?) {
    Column(
        modifier = Modifier.fillMaxSize().padding(32.dp),
        horizontalAlignment = Alignment.CenterHorizontally,
        verticalArrangement = Arrangement.Center,
    ) {
        Text(
            text = "引擎暂不可用",
            style = MaterialTheme.typography.titleMedium,
            fontWeight = FontWeight.SemiBold,
            color = MaterialTheme.colorScheme.error,
        )
        message?.let {
            Spacer(Modifier.height(8.dp))
            Text(
                text = it,
                style = MaterialTheme.typography.bodySmall,
                color = MaterialTheme.colorScheme.onSurfaceVariant,
            )
        }
    }
}

@Composable
private fun ChatContent(
    state: ChatUiState,
    onRetry: (String) -> Unit,
    onRetryLast: () -> Unit,
    onApproval: (String) -> Unit,
    onCopy: (String) -> Unit,
    onFeedback: (Message, String) -> Unit,
    onToggleFavorite: (Message) -> Unit,
    onToggleSpeak: (Message) -> Unit,
    onJumpToQuestion: (Int) -> Unit,
    onClearConversation: () -> Unit,
    onFetchAttachmentBytes: suspend (String) -> ByteArray?,
    onViewAttachment: (Attachment) -> Unit,
    listState: LazyListState,
    modifier: Modifier = Modifier,
) {
    val showStreamingBubble = state.isStreaming || state.streamingContent.isNotEmpty()
    val isEmpty = state.messages.isEmpty() && !showStreamingBubble && state.thinkingText.isEmpty() && state.toolCalls.isEmpty() && state.approvalPending == null

    if (isEmpty) {
        Box(modifier = modifier.fillMaxSize(), contentAlignment = Alignment.Center) {
            Text(
                text = stringResource(R.string.composer_placeholder),
                style = MaterialTheme.typography.bodySmall,
                color = MaterialTheme.colorScheme.onSurfaceVariant,
            )
        }
        return
    }

    // 最后一条 AI 回复：流式未开始时才显示 actions。流式气泡占据"最新回复"位置时，前一条 AI 消息不再是最新。
    val lastAssistantIdx = state.messages.indexOfLast { it.role == "assistant" }
    val showLastAssistantActions = !showStreamingBubble && lastAssistantIdx >= 0

    LazyColumn(
        state = listState,
        modifier = modifier.fillMaxSize(),
        contentPadding = androidx.compose.foundation.layout.PaddingValues(horizontal = 16.dp, vertical = 12.dp),
        verticalArrangement = Arrangement.spacedBy(12.dp),
    ) {
        itemsIndexed(state.messages, key = { idx, _ -> "msg-$idx" }) { idx, msg ->
            val msgRef = MessageFeedbackRepository.messageRefOf(msg)
            val feedback = state.feedback[msgRef]
            val favored = state.favorites.contains(msgRef)
            val speaking = state.speakingRef != null && state.speakingRef == msgRef
            MessageBubble(
                msg = msg,
                isLastAssistant = showLastAssistantActions && idx == lastAssistantIdx,
                feedback = feedback,
                favored = favored,
                speaking = speaking,
                onCopy = { onCopy(msg.content.orEmpty()) },
                onFeedback = { rating -> onFeedback(msg, rating) },
                onToggleFavorite = { onToggleFavorite(msg) },
                onToggleSpeak = { onToggleSpeak(msg) },
                onJumpToQuestion = { onJumpToQuestion(idx) },
                onFetchAttachmentBytes = onFetchAttachmentBytes,
                onViewAttachment = onViewAttachment,
            )
        }
        // 中间过程在回复之前（对齐 hermes TUI：先思考/工具、后回复）；回复开始流出后思考即收起
        if (state.thinkingText.isNotEmpty() || state.toolCalls.isNotEmpty() || state.activityEvents.isNotEmpty()) {
            item(key = "process") {
                IntermediateProcess(
                    thinking = state.thinkingText.takeIf { it.isNotBlank() },
                    thinkingStatus = if (state.streamingContent.isEmpty()) ThinkingStatus.THINKING else ThinkingStatus.DONE,
                    toolCalls = state.toolCalls,
                    activityEvents = state.activityEvents,
                    defaultExpanded = false,
                )
            }
        }
        if (showStreamingBubble) {
            item(key = "streaming-bubble") { StreamingBubble(content = state.streamingContent) }
        }
        state.approvalPending?.let { ap ->
            item(key = "approval-${ap.runId}") {
                ApprovalCard(
                    command = ap.command,
                    description = ap.description,
                    choices = ap.choices,
                    onSubmit = onApproval,
                    submitting = ap.submitting,
                    responded = ap.responded,
                    respondedChoice = ap.respondedChoice,
                )
            }
        }
        if (state.retryable && state.error != null) {
            item(key = "retry-banner") {
                RetryBanner(message = state.error, onRetry = onRetryLast)
            }
        }
    }
}

@OptIn(ExperimentalMaterial3Api::class, ExperimentalFoundationApi::class)
@Composable
private fun MessageBubble(
    msg: Message,
    isLastAssistant: Boolean,
    feedback: String?,
    favored: Boolean,
    speaking: Boolean,
    onCopy: () -> Unit,
    onFeedback: (String) -> Unit,
    onToggleFavorite: () -> Unit,
    onToggleSpeak: () -> Unit,
    onJumpToQuestion: () -> Unit,
    onFetchAttachmentBytes: suspend (String) -> ByteArray?,
    onViewAttachment: (Attachment) -> Unit,
) {
    val isUser = msg.role == "user"
    val alignment = if (isUser) Alignment.End else Alignment.Start
    val bubbleColor = if (isUser) MaterialTheme.colorScheme.primary else MaterialTheme.colorScheme.surface
    val textColor = if (isUser) MaterialTheme.colorScheme.onPrimary else MaterialTheme.colorScheme.onSurface
    val bubbleShape = if (isUser) {
        RoundedCornerShape(topStart = 16.dp, topEnd = 16.dp, bottomStart = 16.dp, bottomEnd = 4.dp)
    } else {
        RoundedCornerShape(topStart = 16.dp, topEnd = 16.dp, bottomStart = 4.dp, bottomEnd = 16.dp)
    }
    var showMenu by remember { mutableStateOf(false) }
    var showSelectText by remember { mutableStateOf(false) }
    var touchPointInWindow by remember { mutableStateOf(IntOffset.Zero) }
    var bubbleBoundsInWindow by remember { mutableStateOf(Rect.Zero) }
    BoxWithConstraints(modifier = Modifier.fillMaxWidth()) {
        // 气泡最大宽度 = 行宽（即「智能体回复当前宽度」），但不强制 fillMaxWidth
        // → 内容短时气泡缩进到文本长度；内容长时撑满到行宽后 TextView 自动换行
        val maxBubbleWidth = maxWidth
        Column(
            modifier = Modifier.fillMaxWidth(),
            horizontalAlignment = alignment,
        ) {
            // 本次回复的中间过程快照（流式完成时落定）：默认折叠成摘要行，点击展开
            if (!isUser && (msg.liveThinking?.isNotBlank() == true || msg.liveToolCalls.isNotEmpty() || msg.liveActivityEvents.isNotEmpty())) {
                Box(modifier = Modifier.padding(bottom = 6.dp)) {
                    IntermediateProcess(
                        thinking = msg.liveThinking?.takeIf { it.isNotBlank() },
                        thinkingStatus = ThinkingStatus.DONE,
                        toolCalls = msg.liveToolCalls,
                        activityEvents = msg.liveActivityEvents,
                        defaultExpanded = false,
                    )
                }
            }
            Box(
                // Box 不指定 width 约束 → 跟随 Surface 内容尺寸；maxBubbleWidth 由 Surface 的 widthIn 兜底
            ) {
                Surface(
                    color = bubbleColor,
                    shape = bubbleShape,
                    modifier = Modifier
                        .widthIn(max = maxBubbleWidth)
                        .onGloballyPositioned { coords ->
                            val topLeft = coords.localToWindow(Offset.Zero)
                            bubbleBoundsInWindow = Rect(
                                topLeft,
                                topLeft + Offset(coords.size.width.toFloat(), coords.size.height.toFloat()),
                            )
                        }
                        .pointerInput(Unit) {
                            // 长按在触点位置弹出菜单（不再用 combinedClickable 的 onLongClick，
                            // 那拿不到触点坐标；这里 detectTapGestures 给 offset，加上 bubble 在窗口的位置
                            // 得到窗口坐标，再传给 TouchPointPopupPositionProvider 定位）。
                            detectTapGestures(
                                onLongPress = { offset ->
                                    if (showSelectText) return@detectTapGestures
                                    val topLeft = bubbleBoundsInWindow.topLeft
                                    touchPointInWindow = IntOffset(
                                        (topLeft.x + offset.x).roundToInt(),
                                        (topLeft.y + offset.y).roundToInt(),
                                    )
                                    showMenu = true
                                },
                            )
                        },
                ) {
                    Column(modifier = Modifier.padding(horizontal = 14.dp, vertical = 10.dp)) {
                        if (msg.attachments.isNotEmpty()) {
                            AttachmentGallery(
                                attachments = msg.attachments,
                                onFetchAttachmentBytes = onFetchAttachmentBytes,
                                onViewAttachment = onViewAttachment,
                                modifier = Modifier.padding(bottom = 6.dp),
                            )
                        }
                        val body = msg.content.orEmpty().ifBlank { if (msg.attachments.isEmpty()) "（无内容）" else "" }
                        if (showSelectText) {
                            Column {
                                Row(
                                    modifier = Modifier.fillMaxWidth(),
                                    horizontalArrangement = Arrangement.End,
                                ) {
                                    TextButton(onClick = { showSelectText = false }) {
                                        Text(
                                            "完成",
                                            color = if (isUser) MaterialTheme.colorScheme.onPrimary else MaterialTheme.colorScheme.primary,
                                        )
                                    }
                                }
                                OutlinedTextField(
                                    value = body,
                                    onValueChange = {},
                                    readOnly = true,
                                    modifier = Modifier.fillMaxWidth(),
                                    colors = OutlinedTextFieldDefaults.colors(
                                        focusedBorderColor = Color.Transparent,
                                        unfocusedBorderColor = Color.Transparent,
                                        disabledBorderColor = Color.Transparent,
                                        focusedContainerColor = bubbleColor,
                                        unfocusedContainerColor = bubbleColor,
                                        disabledContainerColor = bubbleColor,
                                        focusedTextColor = textColor,
                                        unfocusedTextColor = textColor,
                                        disabledTextColor = textColor,
                                    ),
                                    textStyle = MaterialTheme.typography.bodyLarge,
                                )
                            }
                            BackHandler { showSelectText = false }
                        } else if (body.isNotEmpty()) {
                            MarkdownText(
                                markdown = body,
                                style = MaterialTheme.typography.bodyLarge.copy(color = textColor),
                            )
                        }
                        if (!isUser) {
                            StatusCard(
                                model = msg.liveModel,
                                durationSec = msg.liveDurationSec,
                                tokens = msg.liveTokens,
                            )
                        }
                    }
                }
                if (showMenu) {
                    val density = LocalDensity.current
                    val marginPx = with(density) { 8.dp.roundToPx() }
                    Popup(
                        popupPositionProvider = TouchPointPopupPositionProvider(
                            touchPoint = touchPointInWindow,
                            marginPx = marginPx,
                        ),
                        onDismissRequest = { showMenu = false },
                        properties = PopupProperties(focusable = true, clippingEnabled = false),
                    ) {
                        Card(
                            shape = RoundedCornerShape(28.dp),
                            colors = CardDefaults.cardColors(
                                containerColor = MaterialTheme.colorScheme.surface,
                            ),
                            elevation = CardDefaults.cardElevation(defaultElevation = 12.dp),
                        ) {
                            Row(
                                modifier = Modifier.padding(horizontal = 8.dp, vertical = 12.dp),
                                horizontalArrangement = Arrangement.spacedBy(8.dp),
                            ) {
                                ActionItem(
                                    icon = Icons.Filled.ContentCopy,
                                    label = "复制",
                                    onClick = { showMenu = false; onCopy() },
                                )
                                if (msg.content.orEmpty().isNotBlank()) {
                                    ActionItem(
                                        icon = Icons.AutoMirrored.Filled.TextSnippet,
                                        label = "选取文字",
                                        onClick = { showMenu = false; showSelectText = true },
                                    )
                                }
                                if (!isUser) {
                                    ActionItem(
                                        icon = Icons.Filled.ThumbUp,
                                        label = if (feedback == "up") "取消赞" else "赞",
                                        isOn = feedback == "up",
                                        onClick = { showMenu = false; onFeedback("up") },
                                    )
                                    ActionItem(
                                        icon = Icons.Filled.ThumbDown,
                                        label = if (feedback == "down") "取消踩" else "踩",
                                        isOn = feedback == "down",
                                        onClick = { showMenu = false; onFeedback("down") },
                                    )
                                    ActionItem(
                                        icon = Icons.Filled.Star,
                                        label = if (favored) "取消收藏" else "收藏",
                                        isOn = favored,
                                        onClick = { showMenu = false; onToggleFavorite() },
                                    )
                                    if (msg.content.orEmpty().isNotBlank()) {
                                        ActionItem(
                                            icon = Icons.AutoMirrored.Filled.VolumeUp,
                                            label = if (speaking) "停止播放" else "朗读",
                                            isOn = speaking,
                                            onClick = { showMenu = false; onToggleSpeak() },
                                        )
                                    }
                                    ActionItem(
                                        icon = Icons.Filled.ArrowUpward,
                                        label = "跳转提问",
                                        onClick = { showMenu = false; onJumpToQuestion() },
                                    )
                                }
                            }
                        }
                    }
                }
            }
            when {
                isUser -> Unit
                isLastAssistant -> LastReplyActions(
                    feedback = feedback,
                    favored = favored,
                    speaking = speaking,
                    onCopy = onCopy,
                    onFeedback = onFeedback,
                    onToggleFavorite = onToggleFavorite,
                    onToggleSpeak = onToggleSpeak,
                    onJumpToQuestion = onJumpToQuestion,
                )
            }
        }
    }
}

@Composable
private fun LastReplyActions(
    feedback: String?,
    favored: Boolean,
    speaking: Boolean,
    onCopy: () -> Unit,
    onFeedback: (String) -> Unit,
    onToggleFavorite: () -> Unit,
    onToggleSpeak: () -> Unit,
    onJumpToQuestion: () -> Unit,
) {
    Column(modifier = Modifier.fillMaxWidth().padding(top = 6.dp)) {
        HorizontalDivider(
            thickness = 0.5.dp,
            color = MaterialTheme.colorScheme.outlineVariant,
        )
        Row(
            modifier = Modifier.padding(top = 4.dp),
            horizontalArrangement = Arrangement.spacedBy(2.dp),
            verticalAlignment = Alignment.CenterVertically,
        ) {
            IconButton(onClick = onJumpToQuestion, modifier = Modifier.size(28.dp)) {
                Icon(
                    Icons.Filled.ArrowUpward,
                    contentDescription = "跳转到提问",
                    modifier = Modifier.size(14.dp),
                    tint = MaterialTheme.colorScheme.onSurfaceVariant,
                )
            }
            val isUp = feedback == "up"
            IconButton(onClick = { onFeedback("up") }, modifier = Modifier.size(28.dp)) {
                Icon(
                    Icons.Filled.ThumbUp,
                    contentDescription = "赞",
                    modifier = Modifier.size(14.dp),
                    tint = if (isUp) MaterialTheme.colorScheme.primary else MaterialTheme.colorScheme.onSurfaceVariant,
                )
            }
            val isDown = feedback == "down"
            IconButton(onClick = { onFeedback("down") }, modifier = Modifier.size(28.dp)) {
                Icon(
                    Icons.Filled.ThumbDown,
                    contentDescription = "踩",
                    modifier = Modifier.size(14.dp),
                    tint = if (isDown) MaterialTheme.colorScheme.error else MaterialTheme.colorScheme.onSurfaceVariant,
                )
            }
            IconButton(onClick = onToggleFavorite, modifier = Modifier.size(28.dp)) {
                Icon(
                    Icons.Filled.Star,
                    contentDescription = if (favored) "取消收藏" else "收藏",
                    modifier = Modifier.size(14.dp),
                    tint = if (favored) MaterialTheme.colorScheme.primary else MaterialTheme.colorScheme.onSurfaceVariant,
                )
            }
            IconButton(onClick = onToggleSpeak, modifier = Modifier.size(28.dp)) {
                Icon(
                    Icons.AutoMirrored.Filled.VolumeUp,
                    contentDescription = if (speaking) "停止播放" else "朗读",
                    modifier = Modifier.size(14.dp),
                    tint = if (speaking) MaterialTheme.colorScheme.primary else MaterialTheme.colorScheme.onSurfaceVariant,
                )
            }
            IconButton(onClick = onCopy, modifier = Modifier.size(28.dp)) {
                Icon(
                    Icons.Filled.ContentCopy,
                    contentDescription = "复制",
                    modifier = Modifier.size(14.dp),
                    tint = MaterialTheme.colorScheme.onSurfaceVariant,
                )
            }
        }
    }
}

private suspend fun LazyListState.scrollToBottom() {
    val target = layoutInfo.totalItemsCount - 1
    if (target < 0) return
    // 与进会话钉底同一写法：scrollToItem + scrollBy(MAX) 才真正钉到末尾。
    // 单用 scrollToItem/animateScrollToItem 对高过视口的最后一条是顶对齐（视图停在回复开头）。
    scrollToItem(target)
    scrollBy(Float.MAX_VALUE)
}
/**
 * 用系统图片查看器全屏查看附件图片。
 * - 本地 URI（刚上传的临时文件）：直接用原 URI。
 * - 后端 path（历史消息）：先下载字节到 app cache，再通过 FileProvider 生成 content URI，
 *    granting read 权限给外部查看器。
 */
private fun viewAttachment(
    context: android.content.Context,
    attachment: Attachment,
    viewModel: ChatViewModel,
    scope: kotlinx.coroutines.CoroutineScope,
) {
    if (!attachment.isImage) return
    scope.launch {
        val uri = if (attachment.localUri != null) {
            Uri.parse(attachment.localUri)
        } else {
            val bytes = viewModel.downloadAttachmentImage(attachment.path) ?: return@launch
            val dir = File(context.cacheDir, "attachments").apply { mkdirs() }
            val file = File(dir, attachment.name.ifBlank { "image.jpg" })
            file.writeBytes(bytes)
            FileProvider.getUriForFile(context, "${context.packageName}.fileprovider", file)
        }
        val intent = Intent(Intent.ACTION_VIEW).apply {
            setDataAndType(uri, attachment.mime ?: "image/*")
            addFlags(Intent.FLAG_GRANT_READ_URI_PERMISSION)
            addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)
        }
        runCatching { context.startActivity(intent) }
    }
}

@Composable
private fun StreamingBubble(content: String) {
    // 空内容不再渲染"思考中"占位——等待状态由中间过程收起栏的"等待回复"事件行承担
    if (content.isEmpty()) return
    BoxWithConstraints(modifier = Modifier.fillMaxWidth()) {
        val maxBubbleWidth = maxWidth
        Column(
            modifier = Modifier.fillMaxWidth(),
            horizontalAlignment = Alignment.Start,
        ) {
            Surface(
                color = MaterialTheme.colorScheme.surface,
                shape = RoundedCornerShape(topStart = 16.dp, topEnd = 16.dp, bottomStart = 4.dp, bottomEnd = 16.dp),
                modifier = Modifier.widthIn(max = maxBubbleWidth),
            ) {
                MarkdownText(
                    markdown = content,
                    streaming = true,
                    modifier = Modifier.padding(horizontal = 14.dp, vertical = 10.dp),
                    style = MaterialTheme.typography.bodyLarge.copy(color = MaterialTheme.colorScheme.onSurface),
                )
            }
        }
    }
}

@Composable
private fun RetryBanner(message: String, onRetry: () -> Unit) {
    Surface(
        color = MaterialTheme.colorScheme.errorContainer,
        shape = RoundedCornerShape(12.dp),
        modifier = Modifier.fillMaxWidth(),
    ) {
        Column(modifier = Modifier.padding(12.dp)) {
            Text(
                text = message,
                style = MaterialTheme.typography.bodySmall,
                color = MaterialTheme.colorScheme.onErrorContainer,
            )
            Spacer(Modifier.height(8.dp))
            Row(horizontalArrangement = Arrangement.End, modifier = Modifier.fillMaxWidth()) {
                androidx.compose.material3.TextButton(onClick = onRetry) { Text("重试") }
            }
        }
    }
}

/**
 * 流式自动重连状态条：黄色背景 + 旋转图标 + "重连中 (N/2)..." 文本。
 * 只在 [ChatUiState.reconnecting] = true 时展示，重连成功/失败后自动消失。
 */
@Composable
private fun ReconnectingBanner(attempt: Int) {
    Surface(
        color = MaterialTheme.colorScheme.secondaryContainer,
        shape = RoundedCornerShape(12.dp),
        modifier = Modifier.fillMaxWidth().padding(horizontal = 12.dp, vertical = 4.dp),
    ) {
        Row(
            modifier = Modifier.padding(horizontal = 12.dp, vertical = 8.dp),
            verticalAlignment = Alignment.CenterVertically,
            horizontalArrangement = Arrangement.spacedBy(8.dp),
        ) {
            CircularProgressIndicator(
                modifier = Modifier.size(14.dp),
                strokeWidth = 2.dp,
                color = MaterialTheme.colorScheme.onSecondaryContainer,
            )
            Text(
                text = "网络断开，重连中 ($attempt/2)...",
                style = MaterialTheme.typography.labelMedium,
                color = MaterialTheme.colorScheme.onSecondaryContainer,
            )
        }
    }
}

/**
 * 长按消息弹出的横向操作卡片（豆包风格）。
 * 每项：圆形 tint 背景图标 + 下方文字 label。位置默认在气泡上方，顶部空间不足时翻转到下方。
 */
@Composable
private fun ActionItem(
    icon: ImageVector,
    label: String,
    onClick: () -> Unit,
    isOn: Boolean = false,
) {
    val tint = if (isOn) MaterialTheme.colorScheme.primary else MaterialTheme.colorScheme.surfaceVariant
    val onTint = if (isOn) MaterialTheme.colorScheme.onPrimary else MaterialTheme.colorScheme.onSurfaceVariant
    Column(
        horizontalAlignment = Alignment.CenterHorizontally,
        modifier = Modifier
            .clickable(onClick = onClick)
            .padding(horizontal = 8.dp, vertical = 4.dp),
    ) {
        Box(
            modifier = Modifier
                .size(36.dp)
                .clip(CircleShape)
                .background(tint),
            contentAlignment = Alignment.Center,
        ) {
            Icon(
                icon,
                contentDescription = null,
                tint = onTint,
                modifier = Modifier.size(18.dp),
            )
        }
        Spacer(Modifier.height(4.dp))
        Text(
            text = label,
            style = MaterialTheme.typography.labelSmall,
            color = MaterialTheme.colorScheme.onSurface,
        )
    }
}

/**
 * 顶栏溢出菜单项：横向 icon + label，与长按菜单 ActionItem 的卡片风格统一。
 * 用 Surface clickable 提供 ripple 反馈。
 */
@Composable
private fun OverflowMenuItem(
    icon: ImageVector,
    label: String,
    onClick: () -> Unit,
    tint: Color = MaterialTheme.colorScheme.onSurface,
) {
    Surface(
        color = Color.Transparent,
        onClick = onClick,
        modifier = Modifier.fillMaxWidth(),
    ) {
        Row(
            modifier = Modifier.padding(horizontal = 16.dp, vertical = 12.dp),
            verticalAlignment = Alignment.CenterVertically,
            horizontalArrangement = Arrangement.spacedBy(12.dp),
        ) {
            Icon(
                icon,
                contentDescription = null,
                tint = tint,
                modifier = Modifier.size(20.dp),
            )
            Text(
                text = label,
                style = MaterialTheme.typography.bodyMedium,
                color = tint,
            )
        }
    }
}

/**
 * 把 Popup 放在长按触点附近：默认在触点上方，顶部空间不足时翻转到下方；
 * 水平方向以触点为中心居中并夹到窗口内。
 */
private class TouchPointPopupPositionProvider(
    private val touchPoint: IntOffset,
    private val marginPx: Int,
) : PopupPositionProvider {
    override fun calculatePosition(
        anchorBounds: IntRect,
        windowSize: IntSize,
        layoutDirection: LayoutDirection,
        popupSize: IntSize,
    ): IntOffset {
        val aboveY = touchPoint.y - popupSize.height - marginPx
        val belowY = touchPoint.y + marginPx
        val y = if (aboveY >= 0) aboveY else belowY
        val maxX = (windowSize.width - popupSize.width).coerceAtLeast(0)
        val x = (touchPoint.x - popupSize.width / 2).coerceIn(0, maxX)
        return IntOffset(x, y)
    }
}

/**
 * Popup 锚定到父布局（anchor）的右下角下方，用于 TopBar 右上角 overflow 按钮：
 * x 与 anchor 右边对齐，y 在 anchor 底边下方 margin 处；屏幕下方不够时翻到上方。
 */
private class AnchorBottomEndPopupPositionProvider(
    private val marginPx: Int,
) : PopupPositionProvider {
    override fun calculatePosition(
        anchorBounds: IntRect,
        windowSize: IntSize,
        layoutDirection: LayoutDirection,
        popupSize: IntSize,
    ): IntOffset {
        val x = (anchorBounds.right - popupSize.width).coerceAtLeast(0)
        val belowY = anchorBounds.bottom + marginPx
        val aboveY = anchorBounds.top - marginPx - popupSize.height
        val y = when {
            belowY + popupSize.height <= windowSize.height -> belowY
            aboveY >= 0 -> aboveY
            else -> belowY
        }
        return IntOffset(x, y)
    }
}

/**
 * 消息气泡内的附件展示：
 * - 本地 Uri（上传后立即渲染时）直接用 Coil 加载缩略图
 * - 无 localUri（历史消息从服务器拉回的）→ 用 produceState 异步从后端下载原始字节，
 *   下载完成前显示图标占位，避免图片消息切换出对话框再进来时丢失缩略图
 */
@Composable
private fun AttachmentGallery(
    attachments: List<Attachment>,
    onFetchAttachmentBytes: suspend (String) -> ByteArray?,
    onViewAttachment: (Attachment) -> Unit,
    modifier: Modifier = Modifier,
) {
    LazyRow(
        modifier = modifier,
        horizontalArrangement = Arrangement.spacedBy(6.dp),
    ) {
        itemsIndexed(attachments, key = { idx, a -> "att-$idx-${a.path}" }) { _, a ->
            Surface(
                color = MaterialTheme.colorScheme.surfaceVariant,
                shape = RoundedCornerShape(10.dp),
                onClick = { if (a.isImage) onViewAttachment(a) },
                modifier = Modifier.size(96.dp),
            ) {
                Box(contentAlignment = Alignment.Center) {
                    if (a.isImage && a.localUri != null) {
                        AsyncImage(
                            model = a.localUri,
                            contentDescription = a.name,
                            contentScale = ContentScale.Crop,
                            modifier = Modifier.fillMaxSize(),
                        )
                    } else if (a.isImage && a.path.isNotEmpty()) {
                        // 历史消息：无 localUri，但有后端 path → 异步下载字节后渲染
                        val bytes by produceState<ByteArray?>(initialValue = null, a.path) {
                            value = onFetchAttachmentBytes(a.path)
                        }
                        if (bytes != null) {
                            AsyncImage(
                                model = bytes,
                                contentDescription = a.name,
                                contentScale = ContentScale.Crop,
                                modifier = Modifier.fillMaxSize(),
                            )
                        } else {
                            CircularProgressIndicator(
                                modifier = Modifier.size(20.dp),
                                strokeWidth = 2.dp,
                                color = MaterialTheme.colorScheme.primary,
                            )
                        }
                    } else if (a.isImage) {
                        Icon(
                            Icons.Filled.Image,
                            contentDescription = a.name,
                            modifier = Modifier.size(28.dp),
                            tint = MaterialTheme.colorScheme.primary,
                        )
                    } else {
                        Icon(
                            Icons.Filled.AttachFile,
                            contentDescription = a.name,
                            modifier = Modifier.size(28.dp),
                            tint = MaterialTheme.colorScheme.primary,
                        )
                    }
                }
            }
        }
    }
}
