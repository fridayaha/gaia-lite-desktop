package com.unionagents.enduser.ui.chat

import android.app.Activity
import android.Manifest
import android.content.Context
import android.content.Intent
import android.content.pm.PackageManager
import android.media.MediaRecorder
import android.net.Uri
import android.os.Build
import android.provider.MediaStore
import android.widget.Toast
import androidx.activity.compose.rememberLauncherForActivityResult
import androidx.activity.result.contract.ActivityResultContracts
import androidx.compose.ui.platform.LocalDensity
import androidx.compose.ui.unit.IntOffset
import androidx.compose.ui.unit.IntRect
import androidx.compose.ui.unit.IntSize
import androidx.compose.ui.unit.LayoutDirection
import androidx.compose.ui.window.Popup
import androidx.compose.ui.window.PopupPositionProvider
import androidx.compose.ui.window.PopupProperties
import androidx.compose.foundation.background
import androidx.compose.foundation.gestures.detectTapGestures
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxHeight
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.layout.widthIn
import androidx.compose.ui.layout.boundsInWindow
import androidx.compose.ui.layout.onGloballyPositioned
import androidx.compose.foundation.lazy.LazyRow
import androidx.compose.foundation.lazy.itemsIndexed
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.text.BasicTextField
import androidx.compose.foundation.text.KeyboardOptions
import androidx.compose.foundation.text.input.TextFieldLineLimits
import androidx.compose.foundation.text.input.clearText
import androidx.compose.foundation.text.input.rememberTextFieldState
import androidx.compose.foundation.text.input.setTextAndPlaceCursorAtEnd
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.Add
import androidx.compose.material.icons.filled.AttachFile
import androidx.compose.material.icons.filled.Close
import androidx.compose.material.icons.filled.Image
import androidx.compose.material.icons.filled.Keyboard
import androidx.compose.material.icons.filled.Mic
import androidx.compose.material.icons.filled.PhotoCamera
import androidx.compose.material.icons.filled.Stop
import androidx.compose.material.icons.filled.Tune
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Surface
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.DisposableEffect
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.produceState
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.runtime.setValue
import androidx.compose.runtime.mutableStateListOf
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.SolidColor
import androidx.compose.ui.graphics.vector.ImageVector
import androidx.compose.ui.input.pointer.pointerInput
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.res.stringResource
import androidx.compose.ui.text.TextStyle
import androidx.compose.ui.text.input.ImeAction
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import androidx.core.content.ContextCompat
import androidx.core.content.FileProvider
import coil.compose.AsyncImage
import com.unionagents.enduser.R
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.delay
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext
import android.provider.OpenableColumns
import java.io.File

@Composable
fun Composer(
    enabled: Boolean,
    currentModel: String?,
    onOpenModelSheet: () -> Unit,
    onSend: (String, List<Uri>) -> Unit,
    isStreaming: Boolean,
    onStop: () -> Unit,
    onTranscribe: suspend (File) -> String?,
) {
    // TextFieldState 版 BasicTextField：旧 String 重载在空字段时长按不出粘贴菜单（手势区塌成零宽），
    // 新 API 的选择/粘贴手势覆盖整个输入区域，空字段也能正常长按粘贴
    val textFieldState = rememberTextFieldState()
    var menuExpanded by remember { mutableStateOf(false) }
    val attachments = remember { mutableStateListOf<Uri>() }
    val context = LocalContext.current
    var pendingCameraUri by remember { mutableStateOf<Uri?>(null) }
    var composerBounds by remember { mutableStateOf<IntRect?>(null) }
    val density = LocalDensity.current
    val panelGapPx = remember(density) { with(density) { 8.dp.roundToPx() } }
    val scope = rememberCoroutineScope()

    // 录音状态
    var isRecording by remember { mutableStateOf(false) }
    var recordingSeconds by remember { mutableStateOf(0) }
    var recorder by remember { mutableStateOf<MediaRecorder?>(null) }
    var recordingFile by remember { mutableStateOf<File?>(null) }

    // 按住说话状态（语音输入 → 识别 → 文字回填，与附件录音相互独立）
    var voiceMode by remember { mutableStateOf(false) }
    var holdRecording by remember { mutableStateOf(false) }
    var holdSeconds by remember { mutableStateOf(0) }
    var holdStartMs by remember { mutableStateOf(0L) }
    var transcribing by remember { mutableStateOf(false) }
    var holdRecorder by remember { mutableStateOf<MediaRecorder?>(null) }
    var holdFile by remember { mutableStateOf<File?>(null) }

    val controlsEnabled = enabled && !isStreaming && !isRecording && !holdRecording

    fun doSend() {
        val t = textFieldState.text.trim().toString()
        if (t.isEmpty() && attachments.isEmpty()) return
        if (!enabled || isStreaming) return
        onSend(t, attachments.toList())
        textFieldState.clearText()
        attachments.clear()
    }

    val startRecordingNow: () -> Unit = {
        val pair = createAudioRecorder(context)
        if (pair != null) {
            recorder = pair.first
            recordingFile = pair.second
            isRecording = true
            recordingSeconds = 0
        } else {
            Toast.makeText(context, "启动录音失败", Toast.LENGTH_SHORT).show()
        }
    }

    val stopRecordingNow: () -> Unit = {
        val r = recorder
        val f = recordingFile
        recorder = null
        recordingFile = null
        isRecording = false
        recordingSeconds = 0
        if (r != null && f != null) {
            val uri = finalizeRecording(r, f, context)
            if (uri != null) {
                attachments.add(uri)
            } else {
                Toast.makeText(context, "保存录音失败", Toast.LENGTH_SHORT).show()
                f.delete()
            }
        }
    }

    val recordPermissionLauncher = rememberLauncherForActivityResult(
        contract = ActivityResultContracts.RequestPermission(),
    ) { granted ->
        if (granted) {
            startRecordingNow()
        } else {
            Toast.makeText(context, "需要录音权限才能发送语音", Toast.LENGTH_SHORT).show()
        }
    }

    val onRecordClick: () -> Unit = {
        when (ContextCompat.checkSelfPermission(context, Manifest.permission.RECORD_AUDIO)) {
            PackageManager.PERMISSION_GRANTED -> startRecordingNow()
            else -> recordPermissionLauncher.launch(Manifest.permission.RECORD_AUDIO)
        }
    }

    // 按住说话：松开后把录音文件交给 onTranscribe 识别，识别文本直接发送（与附件录音相互独立）
    val stopHoldRecording: (Boolean) -> Unit = stopHold@ { transcribe ->
        val r = holdRecorder
        val f = holdFile
        holdRecorder = null
        holdFile = null
        if (!holdRecording) return@stopHold
        holdRecording = false
        holdSeconds = 0
        if (r == null || f == null) return@stopHold
        val durationMs = System.currentTimeMillis() - holdStartMs
        val stopped = runCatching { r.stop(); r.reset(); r.release() }.isSuccess
        if (!stopped) runCatching { r.release() }
        if (!transcribe) {
            f.delete()
            return@stopHold
        }
        if (!stopped || durationMs < 500 || !f.exists()) {
            f.delete()
            Toast.makeText(context, "说话时间太短", Toast.LENGTH_SHORT).show()
            return@stopHold
        }
        transcribing = true
        scope.launch {
            val result = onTranscribe(f)
            transcribing = false
            if (result != null) {
                // 识别成功直接发送（对齐豆包语音输入）；保持语音模式便于连续对话
                textFieldState.setTextAndPlaceCursorAtEnd(result)
                doSend()
            }
        }
    }

    val startHoldRecording: () -> Unit = {
        val pair = createAudioRecorder(context)
        if (pair != null) {
            holdRecorder = pair.first
            holdFile = pair.second
            holdRecording = true
            holdSeconds = 0
            holdStartMs = System.currentTimeMillis()
        } else {
            Toast.makeText(context, "启动录音失败", Toast.LENGTH_SHORT).show()
        }
    }

    // 授权弹窗会打断按住手势，授权后需用户再次按住才开始录音
    val holdPermissionLauncher = rememberLauncherForActivityResult(
        contract = ActivityResultContracts.RequestPermission(),
    ) { granted ->
        if (!granted) {
            Toast.makeText(context, "需要录音权限才能语音输入", Toast.LENGTH_SHORT).show()
        }
    }

    // 录音中计时
    LaunchedEffect(isRecording) {
        if (!isRecording) return@LaunchedEffect
        while (isRecording) {
            delay(1000)
            recordingSeconds++
        }
    }

    // 按住说话计时
    LaunchedEffect(holdRecording) {
        if (!holdRecording) return@LaunchedEffect
        while (holdRecording) {
            delay(1000)
            holdSeconds++
        }
    }

    // 退出composer时若仍在录音，清理资源
    DisposableEffect(Unit) {
        onDispose {
            recorder?.let { releaseRecorder(it) }
            recordingFile?.takeIf { it.exists() }?.delete()
            holdRecorder?.let { releaseRecorder(it) }
            holdFile?.takeIf { it.exists() }?.delete()
        }
    }

    // 相册：ACTION_PICK + MediaStore Images URI，强制走系统 Gallery app（不会落到「最近文件」）
    val galleryLauncher = rememberLauncherForActivityResult(
        contract = ActivityResultContracts.StartActivityForResult(),
    ) { result ->
        if (result.resultCode == Activity.RESULT_OK) {
            val uri = result.data?.data
            if (uri != null) attachments.add(uri)
        }
    }

    // 文件：GetContent("*/*")，走系统文件选择器（这里文件管理器是合理的）
    val fileLauncher = rememberLauncherForActivityResult(
        contract = ActivityResultContracts.GetContent(),
    ) { uri ->
        if (uri != null) attachments.add(uri)
    }

    // 相机：TakePicture，写入 FileProvider URI 指向的临时文件
    val cameraLauncher = rememberLauncherForActivityResult(
        contract = ActivityResultContracts.TakePicture(),
    ) { success ->
        if (success && pendingCameraUri != null) {
            attachments.add(pendingCameraUri!!)
        }
        pendingCameraUri = null
    }

    fun launchGallery() {
        val intent = Intent(Intent.ACTION_PICK).apply {
            setDataAndType(MediaStore.Images.Media.EXTERNAL_CONTENT_URI, "image/*")
        }
        galleryLauncher.launch(intent)
    }

    fun launchCamera() {
        val dir = context.getExternalFilesDir(null) ?: run {
            Toast.makeText(context, "无法访问存储", Toast.LENGTH_SHORT).show()
            return
        }
        val photoFile = File(dir, "CAM_${System.currentTimeMillis()}.jpg")
        val uri = FileProvider.getUriForFile(
            context,
            "${context.packageName}.fileprovider",
            photoFile,
        )
        pendingCameraUri = uri
        cameraLauncher.launch(uri)
    }

    Column(modifier = Modifier.fillMaxWidth()) {
        if (attachments.isNotEmpty()) {
            AttachmentTray(
                attachments = attachments,
                onRemove = { idx -> attachments.removeAt(idx) },
                modifier = Modifier.fillMaxWidth().padding(horizontal = 12.dp, vertical = 4.dp),
            )
        }
        if (isRecording) {
            RecordingBar(
                seconds = recordingSeconds,
                onStop = stopRecordingNow,
                modifier = Modifier.fillMaxWidth().padding(horizontal = 12.dp, vertical = 4.dp),
            )
        }
        Surface(
            color = MaterialTheme.colorScheme.background,
            modifier = Modifier.fillMaxWidth(),
        ) {
            Surface(
                color = MaterialTheme.colorScheme.surfaceVariant,
                shape = RoundedCornerShape(28.dp),
                modifier = Modifier
                    .fillMaxWidth()
                    .padding(horizontal = 12.dp, vertical = 8.dp)
                    .height(56.dp)
                    .onGloballyPositioned { coords ->
                        val b = coords.boundsInWindow()
                        composerBounds = IntRect(
                            b.left.toInt(),
                            b.top.toInt(),
                            b.right.toInt(),
                            b.bottom.toInt(),
                        )
                    },
            ) {
                Row(
                    modifier = Modifier
                        .fillMaxWidth()
                        .padding(horizontal = 4.dp),
                    verticalAlignment = Alignment.CenterVertically,
                    horizontalArrangement = Arrangement.spacedBy(2.dp),
                ) {
                    ComposerLeftAction(
                        isStreaming = isStreaming,
                        onOpenModelSheet = onOpenModelSheet,
                        onStop = onStop,
                    )
                    Box(
                        modifier = Modifier
                            .weight(1f)
                            .fillMaxHeight()
                            .padding(horizontal = 4.dp),
                        contentAlignment = Alignment.CenterStart,
                    ) {
                        if (voiceMode) {
                            HoldToTalkBar(
                                holdRecording = holdRecording,
                                holdSeconds = holdSeconds,
                                transcribing = transcribing,
                                enabled = enabled && !isStreaming,
                                onPressStart = {
                                    when (ContextCompat.checkSelfPermission(context, Manifest.permission.RECORD_AUDIO)) {
                                        PackageManager.PERMISSION_GRANTED -> startHoldRecording()
                                        else -> holdPermissionLauncher.launch(Manifest.permission.RECORD_AUDIO)
                                    }
                                },
                                onPressEnd = { released -> stopHoldRecording(released) },
                                modifier = Modifier.fillMaxWidth(),
                            )
                        } else {
                        BasicTextField(
                            state = textFieldState,
                            modifier = Modifier.fillMaxWidth(),
                            enabled = controlsEnabled,
                            lineLimits = TextFieldLineLimits.MultiLine(maxHeightInLines = 4),
                            textStyle = TextStyle(
                                color = if (controlsEnabled) MaterialTheme.colorScheme.onSurface else MaterialTheme.colorScheme.onSurfaceVariant,
                                fontSize = MaterialTheme.typography.bodyMedium.fontSize,
                                lineHeight = MaterialTheme.typography.bodyMedium.lineHeight,
                                fontFamily = MaterialTheme.typography.bodyMedium.fontFamily,
                            ),
                            cursorBrush = SolidColor(MaterialTheme.colorScheme.primary),
                            keyboardOptions = KeyboardOptions(imeAction = ImeAction.Send),
                            onKeyboardAction = { doSend() },
                            decorator = { innerTextField ->
                                if (textFieldState.text.isEmpty()) {
                                    Text(
                                        text = stringResource(if (isStreaming) R.string.composer_waiting else R.string.composer_placeholder),
                                        color = MaterialTheme.colorScheme.onSurfaceVariant,
                                        style = MaterialTheme.typography.bodyMedium,
                                    )
                                }
                                innerTextField()
                            },
                        )
                        }
                    }
                    // 语音/键盘切换：语音模式下输入区变为「按住 说话」
                    IconButton(
                        onClick = { voiceMode = !voiceMode },
                        enabled = enabled && !isStreaming && !isRecording && !holdRecording && !transcribing,
                        modifier = Modifier.size(48.dp),
                    ) {
                        Icon(
                            if (voiceMode) Icons.Filled.Keyboard else Icons.Filled.Mic,
                            contentDescription = if (voiceMode) "切换键盘输入" else "切换语音输入",
                            tint = if (voiceMode) MaterialTheme.colorScheme.primary else MaterialTheme.colorScheme.onSurfaceVariant,
                            modifier = Modifier.size(24.dp),
                        )
                    }
                    // + 按钮：展开/收起附件面板（拍照/相册/文件/录音）
                    IconButton(
                        onClick = { if (controlsEnabled) menuExpanded = !menuExpanded },
                        enabled = controlsEnabled,
                        modifier = Modifier.size(48.dp),
                    ) {
                        Icon(
                            Icons.Filled.Add,
                            contentDescription = "附件",
                            tint = if (menuExpanded) MaterialTheme.colorScheme.primary else MaterialTheme.colorScheme.onSurfaceVariant,
                            modifier = Modifier.size(24.dp),
                        )
                    }
                }
            }
        }

        // 附件面板：用 Popup 实现，宽度与输入框 Surface 对齐，点击外部/返回键关闭。
        if (menuExpanded && composerBounds != null) {
            Popup(
                popupPositionProvider = ComposerPanelPositionProvider(
                    composerBounds = composerBounds!!,
                    gapPx = panelGapPx,
                ),
                onDismissRequest = { menuExpanded = false },
                properties = PopupProperties(focusable = true),
            ) {
                val panelWidth = with(LocalDensity.current) { composerBounds!!.width.toDp() }
                Surface(
                    color = MaterialTheme.colorScheme.surface,
                    shape = RoundedCornerShape(16.dp),
                    shadowElevation = 2.dp,
                    modifier = Modifier.width(panelWidth),
                ) {
                    AttachmentPanel(
                        onCamera = {
                            menuExpanded = false
                            launchCamera()
                        },
                        onGallery = {
                            menuExpanded = false
                            launchGallery()
                        },
                        onFile = {
                            menuExpanded = false
                            fileLauncher.launch("*/*")
                        },
                        onRecord = {
                            menuExpanded = false
                            onRecordClick()
                        },
                    )
                }
            }
        }
    }
}

@Composable
private fun ComposerLeftAction(
    isStreaming: Boolean,
    onOpenModelSheet: () -> Unit,
    onStop: () -> Unit,
) {
    if (isStreaming) {
        IconButton(onClick = onStop, modifier = Modifier.size(48.dp)) {
            Icon(
                Icons.Filled.Stop,
                contentDescription = stringResource(R.string.composer_stop),
                tint = MaterialTheme.colorScheme.error,
                modifier = Modifier.size(22.dp),
            )
        }
    } else {
        IconButton(onClick = onOpenModelSheet, modifier = Modifier.size(48.dp)) {
            Icon(
                Icons.Filled.Tune,
                contentDescription = stringResource(R.string.model_sheet_title),
                tint = MaterialTheme.colorScheme.onSurfaceVariant,
                modifier = Modifier.size(22.dp),
            )
        }
    }
}

@Composable
private fun AttachmentPanel(
    onCamera: () -> Unit,
    onGallery: () -> Unit,
    onFile: () -> Unit,
    onRecord: () -> Unit,
    modifier: Modifier = Modifier,
) {
    Row(
        modifier = modifier.fillMaxWidth().padding(horizontal = 12.dp, vertical = 14.dp),
        horizontalArrangement = Arrangement.SpaceEvenly,
    ) {
        AttachmentButton(
            label = "拍照",
            icon = Icons.Filled.PhotoCamera,
            color = Color(0xFF3B82F6),
            onClick = onCamera,
        )
        AttachmentButton(
            label = "相册",
            icon = Icons.Filled.Image,
            color = Color(0xFF22C55E),
            onClick = onGallery,
        )
        AttachmentButton(
            label = "文件",
            icon = Icons.Filled.AttachFile,
            color = Color(0xFFF59E0B),
            onClick = onFile,
        )
        AttachmentButton(
            label = "录音",
            icon = Icons.Filled.Mic,
            color = Color(0xFF8B5CF6),
            onClick = onRecord,
        )
    }
}

@Composable
private fun AttachmentButton(
    label: String,
    icon: ImageVector,
    color: Color,
    onClick: () -> Unit,
) {
    Column(
        horizontalAlignment = Alignment.CenterHorizontally,
        verticalArrangement = Arrangement.spacedBy(4.dp),
    ) {
        Surface(
            color = color,
            shape = CircleShape,
            modifier = Modifier.size(48.dp),
            onClick = onClick,
        ) {
            Box(contentAlignment = Alignment.Center, modifier = Modifier.fillMaxWidth().fillMaxHeight()) {
                Icon(
                    icon,
                    contentDescription = label,
                    tint = Color.White,
                    modifier = Modifier.size(22.dp),
                )
            }
        }
        Text(
            text = label,
            style = MaterialTheme.typography.labelSmall,
            color = MaterialTheme.colorScheme.onSurface,
        )
    }
}

@Composable
private fun AttachmentTray(
    attachments: List<Uri>,
    onRemove: (Int) -> Unit,
    modifier: Modifier = Modifier,
) {
    val context = LocalContext.current
    Surface(
        color = MaterialTheme.colorScheme.surface,
        shape = RoundedCornerShape(12.dp),
        modifier = modifier,
    ) {
        LazyRow(
            modifier = Modifier.fillMaxWidth().padding(horizontal = 8.dp, vertical = 6.dp),
            horizontalArrangement = Arrangement.spacedBy(6.dp),
        ) {
            itemsIndexed(attachments, key = { idx, uri -> "attach-$idx-${uri}" }) { idx, uri ->
                val displayName by produceState(
                    initialValue = uri.lastPathSegment ?: uri.toString(),
                    uri,
                ) {
                    value = withContext(Dispatchers.IO) {
                        runCatching {
                            context.contentResolver.query(uri, null, null, null, null)?.use { c ->
                                val i = c.getColumnIndex(OpenableColumns.DISPLAY_NAME)
                                if (i >= 0 && c.moveToFirst()) c.getString(i) else null
                            }
                        }.getOrNull() ?: uri.lastPathSegment ?: uri.toString()
                    }
                }
                val mime = remember(uri) {
                    context.contentResolver.getType(uri) ?: "application/octet-stream"
                }
                val isImage = mime.startsWith("image/")
                val attachmentIcon = when {
                    isImage -> null
                    mime.startsWith("audio/") -> Icons.Filled.Mic
                    else -> Icons.Filled.AttachFile
                }
                Surface(
                    color = MaterialTheme.colorScheme.surfaceVariant,
                    shape = RoundedCornerShape(8.dp),
                ) {
                    Row(
                        modifier = Modifier.padding(horizontal = 6.dp, vertical = 4.dp),
                        verticalAlignment = Alignment.CenterVertically,
                        horizontalArrangement = Arrangement.spacedBy(6.dp),
                    ) {
                        if (isImage) {
                            AsyncImage(
                                model = uri,
                                contentDescription = null,
                                modifier = Modifier.size(32.dp),
                            )
                        } else {
                            Icon(
                                imageVector = attachmentIcon ?: Icons.Filled.AttachFile,
                                contentDescription = null,
                                modifier = Modifier.size(32.dp),
                                tint = MaterialTheme.colorScheme.primary,
                            )
                        }
                        Text(
                            text = displayName,
                            style = MaterialTheme.typography.labelSmall,
                            color = MaterialTheme.colorScheme.onSurface,
                            maxLines = 1,
                            overflow = TextOverflow.Ellipsis,
                            modifier = Modifier.widthIn(max = 160.dp),
                        )
                        IconButton(
                            onClick = { onRemove(idx) },
                            modifier = Modifier.size(20.dp),
                        ) {
                            Icon(
                                Icons.Filled.Close,
                                contentDescription = "移除",
                                modifier = Modifier.size(14.dp),
                                tint = MaterialTheme.colorScheme.onSurfaceVariant,
                            )
                        }
                    }
                }
            }
        }
    }
}

/**
 * 按住说话条：按下开始录音，松开停止并识别；手势被取消（如滑出）则丢弃本次录音。
 */
@Composable
private fun HoldToTalkBar(
    holdRecording: Boolean,
    holdSeconds: Int,
    transcribing: Boolean,
    enabled: Boolean,
    onPressStart: () -> Unit,
    onPressEnd: (Boolean) -> Unit,
    modifier: Modifier = Modifier,
) {
    val gestureEnabled = enabled && !transcribing
    Surface(
        color = when {
            holdRecording -> MaterialTheme.colorScheme.primary
            else -> MaterialTheme.colorScheme.surface
        },
        shape = RoundedCornerShape(24.dp),
        modifier = modifier
            .height(40.dp)
            .pointerInput(gestureEnabled) {
                if (!gestureEnabled) return@pointerInput
                detectTapGestures(
                    onPress = {
                        onPressStart()
                        val released = tryAwaitRelease()
                        onPressEnd(released)
                    },
                )
            },
    ) {
        Box(contentAlignment = Alignment.Center, modifier = Modifier.fillMaxWidth()) {
            Text(
                text = when {
                    transcribing -> "识别中…"
                    holdRecording -> "松开识别 · ${formatDuration(holdSeconds)}"
                    else -> "按住 说话"
                },
                style = MaterialTheme.typography.bodyMedium,
                color = when {
                    holdRecording -> MaterialTheme.colorScheme.onPrimary
                    gestureEnabled -> MaterialTheme.colorScheme.onSurface
                    else -> MaterialTheme.colorScheme.onSurfaceVariant
                },
            )
        }
    }
}

/**
 * 录音中状态条：红色闪烁点 + 已录制时长 + 停止按钮。
 */
@Composable
private fun RecordingBar(
    seconds: Int,
    onStop: () -> Unit,
    modifier: Modifier = Modifier,
) {
    Surface(
        color = MaterialTheme.colorScheme.errorContainer,
        shape = RoundedCornerShape(28.dp),
        modifier = modifier,
    ) {
        Row(
            modifier = Modifier
                .fillMaxWidth()
                .padding(horizontal = 16.dp, vertical = 8.dp),
            verticalAlignment = Alignment.CenterVertically,
            horizontalArrangement = Arrangement.SpaceBetween,
        ) {
            Row(
                verticalAlignment = Alignment.CenterVertically,
                horizontalArrangement = Arrangement.spacedBy(8.dp),
            ) {
                Box(
                    modifier = Modifier
                        .size(10.dp)
                        .clip(CircleShape)
                        .background(MaterialTheme.colorScheme.error),
                )
                Text(
                    text = "录音中 ${formatDuration(seconds)}",
                    style = MaterialTheme.typography.bodyMedium,
                    color = MaterialTheme.colorScheme.onErrorContainer,
                )
            }
            IconButton(onClick = onStop, modifier = Modifier.size(36.dp)) {
                Icon(
                    Icons.Filled.Stop,
                    contentDescription = "停止录音",
                    tint = MaterialTheme.colorScheme.error,
                    modifier = Modifier.size(22.dp),
                )
            }
        }
    }
}

private fun formatDuration(seconds: Int): String {
    val m = seconds / 60
    val s = seconds % 60
    return "%d:%02d".format(m, s)
}

/**
 * 创建并启动 MediaRecorder，返回 recorder 与输出文件；失败返回 null。
 */
@Suppress("DEPRECATION")
private fun createAudioRecorder(context: Context): Pair<MediaRecorder, File>? {
    return try {
        val dir = File(context.cacheDir, "recordings").apply { mkdirs() }
        val file = File(dir, "record_${System.currentTimeMillis()}.m4a")
        val recorder = if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.S) {
            MediaRecorder(context)
        } else {
            MediaRecorder()
        }
        recorder.apply {
            setAudioSource(MediaRecorder.AudioSource.MIC)
            setOutputFormat(MediaRecorder.OutputFormat.MPEG_4)
            setAudioEncoder(MediaRecorder.AudioEncoder.AAC)
            setOutputFile(file.absolutePath)
            prepare()
            start()
        }
        recorder to file
    } catch (e: Exception) {
        null
    }
}

/**
 * 停止录音并返回 FileProvider content URI；失败返回 null 并释放资源。
 */
private fun finalizeRecording(recorder: MediaRecorder, file: File, context: Context): Uri? {
    return try {
        recorder.stop()
        recorder.reset()
        recorder.release()
        FileProvider.getUriForFile(context, "${context.packageName}.fileprovider", file)
    } catch (e: Exception) {
        runCatching { recorder.release() }
        null
    }
}

private fun releaseRecorder(recorder: MediaRecorder) {
    runCatching {
        recorder.stop()
        recorder.reset()
        recorder.release()
    }
}

/**
 * 把附件面板定位在 composer 输入框上方，宽度与输入框 Surface 对齐。
 * @param composerBounds 输入框 Surface 在窗口中的像素边界
 * @param gapPx 面板与输入框之间的像素间距
 */
private class ComposerPanelPositionProvider(
    private val composerBounds: IntRect,
    private val gapPx: Int,
) : PopupPositionProvider {
    override fun calculatePosition(
        anchorBounds: IntRect,
        windowSize: IntSize,
        layoutDirection: LayoutDirection,
        popupContentSize: IntSize,
    ): IntOffset {
        val x = composerBounds.left
        val y = composerBounds.top - popupContentSize.height - gapPx
        return IntOffset(x, y)
    }
}
