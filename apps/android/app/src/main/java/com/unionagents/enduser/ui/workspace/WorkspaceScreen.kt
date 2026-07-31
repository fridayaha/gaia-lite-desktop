package com.unionagents.enduser.ui.workspace

import android.app.Activity
import android.content.Intent
import android.provider.MediaStore
import android.widget.Toast
import androidx.activity.compose.BackHandler
import androidx.activity.compose.rememberLauncherForActivityResult
import androidx.activity.result.contract.ActivityResultContracts
import androidx.compose.foundation.ExperimentalFoundationApi
import androidx.compose.foundation.background
import androidx.compose.foundation.clickable
import androidx.compose.foundation.combinedClickable
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.PaddingValues
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.heightIn
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.automirrored.filled.ArrowBack
import androidx.compose.material.icons.automirrored.filled.DriveFileMove
import androidx.compose.material.icons.filled.Add
import androidx.compose.material.icons.filled.AttachFile
import androidx.compose.material.icons.filled.CheckCircle
import androidx.compose.material.icons.filled.Close
import androidx.compose.material.icons.filled.CreateNewFolder
import androidx.compose.material.icons.filled.Delete
import androidx.compose.material.icons.filled.Description
import androidx.compose.material.icons.filled.Download
import androidx.compose.material.icons.filled.Folder
import androidx.compose.material.icons.filled.Image
import androidx.compose.material.icons.filled.RadioButtonUnchecked
import androidx.compose.material.icons.filled.Search
import androidx.compose.material.icons.filled.SelectAll
import androidx.compose.material3.Card
import androidx.compose.material3.CardDefaults
import androidx.compose.material3.CircularProgressIndicator
import androidx.compose.material3.ExperimentalMaterial3Api
import androidx.compose.material3.HorizontalDivider
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.Scaffold
import androidx.compose.material3.Surface
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.material3.TopAppBar
import androidx.compose.material3.TopAppBarDefaults
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.geometry.Offset
import androidx.compose.ui.geometry.Rect
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.vector.ImageVector
import androidx.compose.ui.layout.onGloballyPositioned
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.platform.LocalDensity
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.IntOffset
import androidx.compose.ui.unit.IntRect
import androidx.compose.ui.unit.IntSize
import androidx.compose.ui.unit.LayoutDirection
import androidx.compose.ui.unit.dp
import androidx.compose.ui.window.Dialog
import androidx.compose.ui.window.Popup
import androidx.compose.ui.window.PopupPositionProvider
import androidx.compose.ui.window.PopupProperties
import androidx.hilt.navigation.compose.hiltViewModel
import androidx.lifecycle.compose.collectAsStateWithLifecycle
import com.unionagents.enduser.net.dto.WorkspaceFileEntry
import kotlinx.coroutines.launch
import kotlin.math.roundToInt

@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun WorkspaceScreen(
    agentId: String? = null,
    showBackButton: Boolean = true,
    agentSelector: @Composable (() -> Unit)? = null,
    onOpenFile: (String) -> Unit,
    onBack: () -> Unit,
    modifier: Modifier = Modifier,
    viewModel: WorkspaceViewModel = hiltViewModel(),
) {
    val ui by viewModel.ui.collectAsStateWithLifecycle()
    val context = LocalContext.current
    val scope = rememberCoroutineScope()

    LaunchedEffect(agentId) {
        agentId?.let { viewModel.setAgentId(it) }
    }

    var showSearch by remember { mutableStateOf(false) }
    var menuExpanded by remember { mutableStateOf(false) }
    var showNewFolder by remember { mutableStateOf(false) }
    var moveTarget by remember { mutableStateOf<WorkspaceFileEntry?>(null) }
    var downloadTarget by remember { mutableStateOf<WorkspaceFileEntry?>(null) }
    var deleteTarget by remember { mutableStateOf<Set<String>?>(null) }
    var actionEntry by remember { mutableStateOf<WorkspaceFileEntry?>(null) }
    var addMenuAnchor by remember { mutableStateOf(Rect.Zero) }

    val imageLauncher = rememberLauncherForActivityResult(
        ActivityResultContracts.StartActivityForResult(),
    ) { result ->
        if (result.resultCode == Activity.RESULT_OK) {
            result.data?.data?.let { uri ->
                viewModel.uploadFile(uri) { ok, msg ->
                    scope.launch {
                        Toast.makeText(
                            context,
                            if (ok) "上传成功" else (msg ?: "上传失败"),
                            Toast.LENGTH_SHORT,
                        ).show()
                    }
                }
            }
        }
    }

    val fileLauncher = rememberLauncherForActivityResult(
        ActivityResultContracts.GetContent(),
    ) { uri ->
        uri?.let {
            viewModel.uploadFile(it) { ok, msg ->
                scope.launch {
                    Toast.makeText(
                        context,
                        if (ok) "上传成功" else (msg ?: "上传失败"),
                        Toast.LENGTH_SHORT,
                    ).show()
                }
            }
        }
    }

    val downloadLauncher = rememberLauncherForActivityResult(
        ActivityResultContracts.CreateDocument("*/*"),
    ) { uri ->
        val target = downloadTarget ?: return@rememberLauncherForActivityResult
        uri ?: return@rememberLauncherForActivityResult
        scope.launch {
            val bytes = viewModel.downloadFile(target.path)
            if (bytes == null) {
                scope.launch {
                    Toast.makeText(context, "下载失败", Toast.LENGTH_SHORT).show()
                }
            } else {
                try {
                    context.contentResolver.openOutputStream(uri)?.use { it.write(bytes) }
                    scope.launch {
                        Toast.makeText(context, "已保存 ${target.name}", Toast.LENGTH_SHORT).show()
                    }
                } catch (_: Throwable) {
                    scope.launch {
                        Toast.makeText(context, "保存失败", Toast.LENGTH_SHORT).show()
                    }
                }
            }
        }
        downloadTarget = null
    }

    BackHandler(enabled = ui.selectionMode) { viewModel.exitSelectionMode() }
    BackHandler(enabled = !ui.selectionMode && ui.stack.size > 1) { viewModel.goBack() }

    Scaffold(
        modifier = modifier,
        containerColor = MaterialTheme.colorScheme.background,
        topBar = {
            TopAppBar(
                title = {
                    when {
                        ui.selectionMode -> {
                            Text(
                                text = "已选 ${ui.selectedPaths.size} 项",
                                fontWeight = FontWeight.SemiBold,
                                style = MaterialTheme.typography.titleMedium,
                            )
                        }
                        agentSelector != null -> agentSelector()
                        else -> {
                            Text(
                                text = when {
                                    ui.developerMode -> "工作区"
                                    else -> "云盘"
                                },
                                fontWeight = FontWeight.SemiBold,
                                style = MaterialTheme.typography.titleMedium,
                            )
                        }
                    }
                },
                navigationIcon = {
                    val showNavBack = ui.selectionMode || ui.stack.size > 1 || showBackButton
                    if (showNavBack) {
                        IconButton(
                            onClick = {
                                when {
                                    ui.selectionMode -> viewModel.exitSelectionMode()
                                    ui.stack.size > 1 -> viewModel.goBack()
                                    else -> onBack()
                                }
                            },
                            modifier = Modifier.size(48.dp),
                        ) {
                            Icon(Icons.AutoMirrored.Filled.ArrowBack, contentDescription = "返回")
                        }
                    }
                },
                actions = {
                    if (!ui.selectionMode) {
                        IconButton(onClick = { showSearch = !showSearch }) {
                            Icon(Icons.Filled.Search, contentDescription = "搜索")
                        }
                        Box(
                            modifier = Modifier.onGloballyPositioned { coords ->
                                val topLeft = coords.localToWindow(Offset.Zero)
                                addMenuAnchor = Rect(
                                    topLeft,
                                    topLeft + Offset(
                                        coords.size.width.toFloat(),
                                        coords.size.height.toFloat(),
                                    ),
                                )
                            },
                        ) {
                            IconButton(onClick = { menuExpanded = true }) {
                                Icon(Icons.Filled.Add, contentDescription = "更多")
                            }
                            if (menuExpanded) {
                                val density = LocalDensity.current
                                val marginPx = with(density) { 8.dp.roundToPx() }
                                Popup(
                                    popupPositionProvider = AnchorBottomEndPopupPositionProvider(
                                        addMenuAnchor,
                                        marginPx,
                                    ),
                                    onDismissRequest = { menuExpanded = false },
                                    properties = PopupProperties(
                                        focusable = true,
                                        clippingEnabled = false,
                                    ),
                                ) {
                                    Card(
                                        shape = RoundedCornerShape(20.dp),
                                        colors = CardDefaults.cardColors(
                                            containerColor = MaterialTheme.colorScheme.surface,
                                        ),
                                        elevation = CardDefaults.cardElevation(
                                            defaultElevation = 8.dp,
                                        ),
                                        modifier = Modifier.width(220.dp),
                                    ) {
                                        Column(
                                            modifier = Modifier.padding(vertical = 4.dp),
                                        ) {
                                            OverflowMenuItem(
                                                icon = Icons.Filled.CreateNewFolder,
                                                label = "新建文件夹",
                                                onClick = {
                                                    menuExpanded = false
                                                    showNewFolder = true
                                                },
                                            )
                                            OverflowMenuItem(
                                                icon = Icons.Filled.Image,
                                                label = "上传图片",
                                                onClick = {
                                                    menuExpanded = false
                                                    val intent = Intent(
                                                        Intent.ACTION_PICK,
                                                        MediaStore.Images.Media.EXTERNAL_CONTENT_URI,
                                                    )
                                                    imageLauncher.launch(intent)
                                                },
                                            )
                                            OverflowMenuItem(
                                                icon = Icons.Filled.AttachFile,
                                                label = "上传文件",
                                                onClick = {
                                                    menuExpanded = false
                                                    fileLauncher.launch("*/*")
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
        },
    ) { padding ->
        Column(
            modifier = Modifier
                .fillMaxSize()
                .background(MaterialTheme.colorScheme.background)
                .padding(padding),
        ) {
            Breadcrumb(stack = ui.stack, onJump = { path -> viewModel.loadDir(path) })

            when {
                ui.selectionMode -> {
                    BatchActionBar(
                        selectedCount = ui.selectedPaths.size,
                        onSelectAll = viewModel::selectAll,
                        onDelete = { deleteTarget = ui.selectedPaths },
                        onCancel = viewModel::exitSelectionMode,
                    )
                }

                showSearch -> {
                    OutlinedTextField(
                        value = ui.searchQuery,
                        onValueChange = viewModel::setSearchQuery,
                        placeholder = {
                            Text("搜索文件", style = MaterialTheme.typography.bodySmall)
                        },
                        leadingIcon = {
                            Icon(
                                Icons.Filled.Search,
                                contentDescription = null,
                                modifier = Modifier.size(18.dp),
                            )
                        },
                        trailingIcon = {
                            if (ui.searchQuery.isNotEmpty()) {
                                IconButton(onClick = { viewModel.setSearchQuery("") }) {
                                    Icon(
                                        Icons.Filled.Close,
                                        contentDescription = "清空",
                                        modifier = Modifier.size(18.dp),
                                    )
                                }
                            }
                        },
                        singleLine = true,
                        textStyle = MaterialTheme.typography.bodySmall,
                        modifier = Modifier
                            .fillMaxWidth()
                            .padding(horizontal = 16.dp, vertical = 8.dp),
                    )
                }
            }

            when {
                ui.loading -> LoadingState()
                ui.error != null -> ErrorState(message = ui.error!!, onRetry = viewModel::refresh)
                ui.filteredEntries.isEmpty() -> EmptyState(searching = ui.searchQuery.isNotBlank())
                else -> FileList(
                    entries = ui.filteredEntries,
                    selectedPaths = ui.selectedPaths,
                    selectionMode = ui.selectionMode,
                    onClick = { entry ->
                        if (ui.selectionMode) {
                            viewModel.toggleSelection(entry.path)
                        } else if (entry.isDir) {
                            viewModel.openEntry(entry)
                        } else {
                            onOpenFile(entry.path)
                        }
                    },
                    onLongClick = { entry -> actionEntry = entry },
                    onDownload = { entry ->
                        downloadTarget = entry
                        downloadLauncher.launch(entry.name)
                    },
                    onMove = { entry -> moveTarget = entry },
                    onDelete = { entry -> deleteTarget = setOf(entry.path) },
                    onEnterSelection = { entry -> viewModel.enterSelectionMode(entry.path) },
                )
            }
        }
    }

    if (showNewFolder) {
        NewFolderDialog(
            existingNames = ui.entries.map { it.name }.toSet() +
                if (!ui.developerMode && ui.path == ".") WORKSPACE_HIDDEN_DIRS else emptySet(),
            onDismiss = { showNewFolder = false },
            onConfirm = { name ->
                viewModel.createFolder(name) { ok, msg ->
                    if (!ok) {
                        scope.launch {
                            Toast.makeText(
                                context,
                                msg ?: "创建失败",
                                Toast.LENGTH_SHORT,
                            ).show()
                        }
                    }
                }
                showNewFolder = false
            },
        )
    }

    moveTarget?.let { entry ->
        MoveToDialog(
            currentPath = ui.path,
            dirs = ui.entries.filter { it.isDir && it.path != entry.path },
            onDismiss = { moveTarget = null },
            onMove = { dir ->
                val toPath = if (ui.path == ".") dir.name else "${ui.path}/${dir.name}"
                viewModel.moveFile(entry.path, toPath) { ok, msg ->
                    if (!ok) {
                        scope.launch {
                            Toast.makeText(
                                context,
                                msg ?: "移动失败",
                                Toast.LENGTH_SHORT,
                            ).show()
                        }
                    }
                }
                moveTarget = null
            },
        )
    }

    actionEntry?.let { entry ->
        FileActionDialog(
            entry = entry,
            onDismiss = { actionEntry = null },
            onDownload = {
                actionEntry = null
                downloadTarget = entry
                downloadLauncher.launch(entry.name)
            },
            onMultiSelect = {
                actionEntry = null
                viewModel.enterSelectionMode(entry.path)
            },
            onMove = {
                actionEntry = null
                moveTarget = entry
            },
            onDelete = {
                actionEntry = null
                deleteTarget = setOf(entry.path)
            },
        )
    }

    deleteTarget?.let { paths ->
        ConfirmDeleteDialog(
            count = paths.size,
            onDismiss = { deleteTarget = null },
            onConfirm = {
                paths.forEach { path ->
                    viewModel.deleteFile(path) { ok, msg ->
                        if (!ok) {
                            scope.launch {
                                Toast.makeText(
                                    context,
                                    msg ?: "删除失败",
                                    Toast.LENGTH_SHORT,
                                ).show()
                            }
                        }
                    }
                }
                viewModel.exitSelectionMode()
                deleteTarget = null
            },
        )
    }
}

@Composable
private fun Breadcrumb(stack: List<String>, onJump: (String) -> Unit) {
    Row(
        modifier = Modifier.fillMaxWidth().padding(horizontal = 16.dp, vertical = 8.dp),
        horizontalArrangement = Arrangement.spacedBy(4.dp),
        verticalAlignment = Alignment.CenterVertically,
    ) {
        stack.forEachIndexed { idx, p ->
            if (idx > 0) {
                Text(
                    "/",
                    color = MaterialTheme.colorScheme.onSurfaceVariant,
                    style = MaterialTheme.typography.labelMedium,
                )
            }
            val label = if (p == ".") "根" else p.substringAfterLast('/')
            val isLast = idx == stack.lastIndex
            Surface(
                onClick = { onJump(p) },
                shape = RoundedCornerShape(8.dp),
                color = if (isLast) MaterialTheme.colorScheme.primaryContainer else Color.Transparent,
            ) {
                Text(
                    text = label,
                    style = MaterialTheme.typography.labelMedium,
                    fontWeight = if (isLast) FontWeight.SemiBold else FontWeight.Normal,
                    color = if (isLast) MaterialTheme.colorScheme.onPrimaryContainer else MaterialTheme.colorScheme.primary,
                    modifier = Modifier.padding(horizontal = 8.dp, vertical = 4.dp),
                )
            }
        }
    }
}

@Composable
private fun LoadingState() {
    Box(modifier = Modifier.fillMaxSize(), contentAlignment = Alignment.Center) {
        CircularProgressIndicator()
    }
}

@Composable
private fun ErrorState(message: String, onRetry: () -> Unit) {
    Column(
        modifier = Modifier.fillMaxSize().padding(32.dp),
        horizontalAlignment = Alignment.CenterHorizontally,
        verticalArrangement = Arrangement.Center,
    ) {
        Text(
            text = "加载失败",
            style = MaterialTheme.typography.titleMedium,
            fontWeight = FontWeight.SemiBold,
        )
        Text(
            text = message,
            style = MaterialTheme.typography.bodySmall,
            color = MaterialTheme.colorScheme.onSurfaceVariant,
            modifier = Modifier.padding(top = 4.dp),
        )
        Surface(
            onClick = onRetry,
            shape = RoundedCornerShape(10.dp),
            color = MaterialTheme.colorScheme.primary,
            modifier = Modifier.padding(top = 12.dp),
        ) {
            Text(
                "重试",
                color = MaterialTheme.colorScheme.onPrimary,
                style = MaterialTheme.typography.labelLarge,
                fontWeight = FontWeight.Medium,
                modifier = Modifier.padding(horizontal = 16.dp, vertical = 8.dp),
            )
        }
    }
}

@Composable
private fun EmptyState(searching: Boolean) {
    Column(
        modifier = Modifier.fillMaxSize().padding(32.dp),
        horizontalAlignment = Alignment.CenterHorizontally,
        verticalArrangement = Arrangement.Center,
    ) {
        Text(
            text = if (searching) "未找到匹配的文件" else "工作区为空",
            style = MaterialTheme.typography.bodyMedium,
            color = MaterialTheme.colorScheme.onSurfaceVariant,
        )
    }
}

@Composable
private fun FileList(
    entries: List<WorkspaceFileEntry>,
    selectedPaths: Set<String>,
    selectionMode: Boolean,
    onClick: (WorkspaceFileEntry) -> Unit,
    onLongClick: (WorkspaceFileEntry) -> Unit,
    onDownload: (WorkspaceFileEntry) -> Unit,
    onMove: (WorkspaceFileEntry) -> Unit,
    onDelete: (WorkspaceFileEntry) -> Unit,
    onEnterSelection: (WorkspaceFileEntry) -> Unit,
) {
    LazyColumn(
        modifier = Modifier.fillMaxSize(),
        contentPadding = PaddingValues(horizontal = 12.dp, vertical = 4.dp),
        verticalArrangement = Arrangement.spacedBy(6.dp),
    ) {
        items(entries, key = { it.path }) { entry ->
            FileRow(
                entry = entry,
                isSelected = entry.path in selectedPaths,
                selectionMode = selectionMode,
                onClick = { onClick(entry) },
                onLongClick = { onLongClick(entry) },
            )
        }
    }
}

@OptIn(ExperimentalFoundationApi::class)
@Composable
private fun FileRow(
    entry: WorkspaceFileEntry,
    isSelected: Boolean,
    selectionMode: Boolean,
    onClick: () -> Unit,
    onLongClick: () -> Unit,
) {
    val containerColor = when {
        selectionMode && isSelected -> MaterialTheme.colorScheme.primaryContainer
        else -> MaterialTheme.colorScheme.surface
    }
    val onContainerColor = if (selectionMode && isSelected) {
        MaterialTheme.colorScheme.onPrimaryContainer
    } else {
        MaterialTheme.colorScheme.onSurface
    }

    Surface(
        shape = RoundedCornerShape(14.dp),
        color = containerColor,
        shadowElevation = 1.dp,
        modifier = Modifier
            .fillMaxWidth()
            .combinedClickable(
                onClick = onClick,
                onLongClick = if (selectionMode) null else onLongClick,
            ),
    ) {
        Row(
            verticalAlignment = Alignment.CenterVertically,
            modifier = Modifier.padding(horizontal = 12.dp, vertical = 10.dp),
        ) {
            if (selectionMode) {
                Icon(
                    if (isSelected) Icons.Filled.CheckCircle else Icons.Filled.RadioButtonUnchecked,
                    contentDescription = null,
                    tint = if (isSelected) MaterialTheme.colorScheme.primary else MaterialTheme.colorScheme.onSurfaceVariant,
                    modifier = Modifier.size(22.dp),
                )
                Spacer(Modifier.size(10.dp))
            } else {
                val iconBg = if (entry.isDir) MaterialTheme.colorScheme.primary else MaterialTheme.colorScheme.surfaceVariant
                val iconTint = if (entry.isDir) MaterialTheme.colorScheme.onPrimary else MaterialTheme.colorScheme.onSurfaceVariant
                Surface(
                    shape = RoundedCornerShape(10.dp),
                    color = iconBg,
                    modifier = Modifier.size(34.dp),
                ) {
                    Box(contentAlignment = Alignment.Center) {
                        Icon(
                            if (entry.isDir) Icons.Filled.Folder else Icons.Filled.Description,
                            contentDescription = null,
                            tint = iconTint,
                            modifier = Modifier.size(18.dp),
                        )
                    }
                }
                Spacer(Modifier.size(10.dp))
            }
            Column(modifier = Modifier.weight(1f)) {
                Text(
                    text = entry.name,
                    style = MaterialTheme.typography.bodyMedium,
                    fontWeight = FontWeight.Medium,
                    maxLines = 1,
                    overflow = TextOverflow.Ellipsis,
                    color = onContainerColor,
                )
                if (!entry.isDir && entry.size > 0) {
                    Text(
                        text = formatSize(entry.size),
                        style = MaterialTheme.typography.labelSmall,
                        color = if (selectionMode && isSelected) MaterialTheme.colorScheme.onPrimaryContainer else MaterialTheme.colorScheme.onSurfaceVariant,
                        modifier = Modifier.padding(top = 2.dp),
                    )
                }
            }
        }
    }
}

/**
 * 顶栏溢出菜单项：横向 icon + label，与对话界面三点菜单风格统一。
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
 * Popup 锚定到父布局（anchor）的右下角下方，用于 TopBar 右上角按钮。
 */
private class AnchorBottomEndPopupPositionProvider(
    private val anchor: Rect,
    private val marginPx: Int,
) : PopupPositionProvider {
    override fun calculatePosition(
        anchorBounds: IntRect,
        windowSize: IntSize,
        layoutDirection: LayoutDirection,
        popupSize: IntSize,
    ): IntOffset {
        val right = anchor.right.roundToInt()
        val bottom = anchor.bottom.roundToInt()
        val top = anchor.top.roundToInt()
        val x = (right - popupSize.width).coerceAtLeast(0)
        val belowY = bottom + marginPx
        val aboveY = top - marginPx - popupSize.height
        val y = when {
            belowY + popupSize.height <= windowSize.height -> belowY
            aboveY >= 0 -> aboveY
            else -> belowY
        }
        return IntOffset(x, y)
    }
}

@Composable
private fun BatchActionBar(
    selectedCount: Int,
    onSelectAll: () -> Unit,
    onDelete: () -> Unit,
    onCancel: () -> Unit,
) {
    Surface(
        shape = RoundedCornerShape(12.dp),
        color = MaterialTheme.colorScheme.primaryContainer,
        modifier = Modifier
            .fillMaxWidth()
            .padding(horizontal = 12.dp, vertical = 4.dp),
    ) {
        Row(
            modifier = Modifier.padding(horizontal = 8.dp, vertical = 6.dp),
            verticalAlignment = Alignment.CenterVertically,
            horizontalArrangement = Arrangement.SpaceBetween,
        ) {
            Text(
                text = "已选 $selectedCount",
                style = MaterialTheme.typography.labelLarge,
                fontWeight = FontWeight.Medium,
                color = MaterialTheme.colorScheme.onPrimaryContainer,
                modifier = Modifier.padding(start = 4.dp),
            )
            Row(verticalAlignment = Alignment.CenterVertically) {
                TextButton(onClick = onSelectAll) {
                    Icon(
                        Icons.Filled.SelectAll,
                        contentDescription = null,
                        modifier = Modifier.size(16.dp),
                    )
                    Spacer(Modifier.size(4.dp))
                    Text("全选")
                }
                TextButton(onClick = onDelete, enabled = selectedCount > 0) {
                    Icon(
                        Icons.Filled.Delete,
                        contentDescription = null,
                        modifier = Modifier.size(16.dp),
                        tint = MaterialTheme.colorScheme.error,
                    )
                    Spacer(Modifier.size(4.dp))
                    Text("删除", color = MaterialTheme.colorScheme.error)
                }
                TextButton(onClick = onCancel) {
                    Icon(
                        Icons.Filled.Close,
                        contentDescription = null,
                        modifier = Modifier.size(16.dp),
                    )
                    Spacer(Modifier.size(4.dp))
                    Text("取消")
                }
            }
        }
    }
}

@Composable
private fun NewFolderDialog(
    existingNames: Set<String>,
    onDismiss: () -> Unit,
    onConfirm: (String) -> Unit,
) {
    var name by remember { mutableStateOf("") }
    var error by remember { mutableStateOf<String?>(null) }

    Dialog(onDismissRequest = onDismiss) {
        Surface(
            shape = RoundedCornerShape(20.dp),
            color = MaterialTheme.colorScheme.surface,
            tonalElevation = 0.dp,
            shadowElevation = 6.dp,
            modifier = Modifier.fillMaxWidth(),
        ) {
            Column(modifier = Modifier.padding(20.dp)) {
                Row(verticalAlignment = Alignment.CenterVertically) {
                    Surface(
                        shape = RoundedCornerShape(10.dp),
                        color = MaterialTheme.colorScheme.primaryContainer,
                        modifier = Modifier.size(32.dp),
                    ) {
                        Box(contentAlignment = Alignment.Center) {
                            Icon(
                                Icons.Filled.CreateNewFolder,
                                contentDescription = null,
                                tint = MaterialTheme.colorScheme.onPrimaryContainer,
                                modifier = Modifier.size(18.dp),
                            )
                        }
                    }
                    Spacer(Modifier.size(12.dp))
                    Text(
                        text = "新建文件夹",
                        style = MaterialTheme.typography.titleMedium,
                        fontWeight = FontWeight.SemiBold,
                    )
                }
                Spacer(Modifier.size(16.dp))
                OutlinedTextField(
                    value = name,
                    onValueChange = {
                        name = it
                        error = null
                    },
                    label = { Text("文件夹名称") },
                    isError = error != null,
                    supportingText = error?.let { { Text(it) } },
                    singleLine = true,
                    shape = RoundedCornerShape(12.dp),
                    textStyle = MaterialTheme.typography.bodyMedium,
                    modifier = Modifier.fillMaxWidth(),
                )
                Spacer(Modifier.size(20.dp))
                Row(
                    modifier = Modifier.fillMaxWidth(),
                    horizontalArrangement = Arrangement.End,
                    verticalAlignment = Alignment.CenterVertically,
                ) {
                    TextButton(onClick = onDismiss) { Text("取消") }
                    Spacer(Modifier.size(8.dp))
                    Surface(
                        onClick = {
                            val trimmed = name.trim()
                            error = when {
                                trimmed.isEmpty() -> "名称不能为空"
                                trimmed in existingNames -> "名称已存在"
                                '/' in trimmed || '\\' in trimmed -> "名称包含非法字符"
                                trimmed.startsWith('.') -> "名称不能以 . 开头"
                                else -> null
                            }
                            if (error == null) {
                                onConfirm(trimmed)
                            }
                        },
                        shape = RoundedCornerShape(10.dp),
                        color = MaterialTheme.colorScheme.primary,
                    ) {
                        Text(
                            text = "确定",
                            color = MaterialTheme.colorScheme.onPrimary,
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

@Composable
private fun MoveToDialog(
    currentPath: String,
    dirs: List<WorkspaceFileEntry>,
    onDismiss: () -> Unit,
    onMove: (WorkspaceFileEntry) -> Unit,
) {
    Dialog(onDismissRequest = onDismiss) {
        Surface(
            shape = RoundedCornerShape(20.dp),
            color = MaterialTheme.colorScheme.surface,
            tonalElevation = 0.dp,
            shadowElevation = 6.dp,
            modifier = Modifier.fillMaxWidth(),
        ) {
            Column(modifier = Modifier.padding(20.dp)) {
                Row(verticalAlignment = Alignment.CenterVertically) {
                    Surface(
                        shape = RoundedCornerShape(10.dp),
                        color = MaterialTheme.colorScheme.primaryContainer,
                        modifier = Modifier.size(32.dp),
                    ) {
                        Box(contentAlignment = Alignment.Center) {
                            Icon(
                                Icons.Filled.Folder,
                                contentDescription = null,
                                tint = MaterialTheme.colorScheme.onPrimaryContainer,
                                modifier = Modifier.size(18.dp),
                            )
                        }
                    }
                    Spacer(Modifier.size(12.dp))
                    Text(
                        text = "移动到",
                        style = MaterialTheme.typography.titleMedium,
                        fontWeight = FontWeight.SemiBold,
                    )
                }
                Spacer(Modifier.size(16.dp))
                if (dirs.isEmpty()) {
                    Text(
                        "当前目录没有子文件夹",
                        style = MaterialTheme.typography.bodyMedium,
                        color = MaterialTheme.colorScheme.onSurfaceVariant,
                    )
                } else {
                    LazyColumn(
                        modifier = Modifier
                            .fillMaxWidth()
                            .heightIn(max = 360.dp),
                        verticalArrangement = Arrangement.spacedBy(6.dp),
                    ) {
                        items(dirs, key = { it.path }) { dir ->
                            Surface(
                                onClick = { onMove(dir) },
                                shape = RoundedCornerShape(12.dp),
                                color = MaterialTheme.colorScheme.surfaceVariant,
                                modifier = Modifier.fillMaxWidth(),
                            ) {
                                Row(
                                    modifier = Modifier.padding(
                                        horizontal = 12.dp,
                                        vertical = 10.dp,
                                    ),
                                    verticalAlignment = Alignment.CenterVertically,
                                ) {
                                    Surface(
                                        shape = RoundedCornerShape(10.dp),
                                        color = MaterialTheme.colorScheme.primary,
                                        modifier = Modifier.size(34.dp),
                                    ) {
                                        Box(contentAlignment = Alignment.Center) {
                                            Icon(
                                                Icons.Filled.Folder,
                                                contentDescription = null,
                                                tint = MaterialTheme.colorScheme.onPrimary,
                                                modifier = Modifier.size(18.dp),
                                            )
                                        }
                                    }
                                    Spacer(Modifier.size(10.dp))
                                    Text(
                                        text = dir.name,
                                        style = MaterialTheme.typography.bodyMedium,
                                        fontWeight = FontWeight.Medium,
                                        modifier = Modifier.weight(1f),
                                    )
                                }
                            }
                        }
                    }
                }
                Spacer(Modifier.size(20.dp))
                Row(
                    modifier = Modifier.fillMaxWidth(),
                    horizontalArrangement = Arrangement.End,
                    verticalAlignment = Alignment.CenterVertically,
                ) {
                    TextButton(onClick = onDismiss) { Text("取消") }
                }
            }
        }
    }
}

@Composable
private fun FileActionDialog(
    entry: WorkspaceFileEntry,
    onDismiss: () -> Unit,
    onDownload: () -> Unit,
    onMultiSelect: () -> Unit,
    onMove: () -> Unit,
    onDelete: () -> Unit,
) {
    Dialog(onDismissRequest = onDismiss) {
        Surface(
            shape = RoundedCornerShape(20.dp),
            color = MaterialTheme.colorScheme.surface,
            tonalElevation = 0.dp,
            shadowElevation = 6.dp,
            modifier = Modifier.fillMaxWidth(),
        ) {
            Column(modifier = Modifier.padding(20.dp)) {
                Row(verticalAlignment = Alignment.CenterVertically) {
                    Surface(
                        shape = RoundedCornerShape(10.dp),
                        color = MaterialTheme.colorScheme.primaryContainer,
                        modifier = Modifier.size(32.dp),
                    ) {
                        Box(contentAlignment = Alignment.Center) {
                            Icon(
                                Icons.Filled.Description,
                                contentDescription = null,
                                tint = MaterialTheme.colorScheme.onPrimaryContainer,
                                modifier = Modifier.size(18.dp),
                            )
                        }
                    }
                    Spacer(Modifier.size(12.dp))
                    Text(
                        text = entry.name,
                        style = MaterialTheme.typography.titleMedium,
                        fontWeight = FontWeight.SemiBold,
                        maxLines = 1,
                        overflow = TextOverflow.Ellipsis,
                        modifier = Modifier.weight(1f),
                    )
                }
                Spacer(Modifier.size(12.dp))
                if (!entry.isDir) {
                    FileActionRow(
                        icon = Icons.Filled.Download,
                        label = "下载",
                        onClick = { onDismiss(); onDownload() },
                    )
                }
                FileActionRow(
                    icon = Icons.Filled.CheckCircle,
                    label = "多选",
                    onClick = { onDismiss(); onMultiSelect() },
                )
                FileActionRow(
                    icon = Icons.AutoMirrored.Filled.DriveFileMove,
                    label = "移动到",
                    onClick = { onDismiss(); onMove() },
                )
                FileActionRow(
                    icon = Icons.Filled.Delete,
                    label = "删除",
                    tint = MaterialTheme.colorScheme.error,
                    onClick = { onDismiss(); onDelete() },
                )
                Spacer(Modifier.size(8.dp))
                Row(
                    modifier = Modifier.fillMaxWidth(),
                    horizontalArrangement = Arrangement.End,
                    verticalAlignment = Alignment.CenterVertically,
                ) {
                    TextButton(onClick = onDismiss) { Text("取消") }
                }
            }
        }
    }
}

@Composable
private fun FileActionRow(
    icon: ImageVector,
    label: String,
    onClick: () -> Unit,
    tint: Color = MaterialTheme.colorScheme.onSurface,
) {
    Surface(
        onClick = onClick,
        shape = RoundedCornerShape(12.dp),
        color = Color.Transparent,
        modifier = Modifier.fillMaxWidth(),
    ) {
        Row(
            modifier = Modifier.padding(horizontal = 8.dp, vertical = 12.dp),
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

@Composable
private fun ConfirmDeleteDialog(
    count: Int,
    onDismiss: () -> Unit,
    onConfirm: () -> Unit,
) {
    Dialog(onDismissRequest = onDismiss) {
        Surface(
            shape = RoundedCornerShape(20.dp),
            color = MaterialTheme.colorScheme.surface,
            tonalElevation = 0.dp,
            shadowElevation = 6.dp,
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
                                Icons.Filled.Delete,
                                contentDescription = null,
                                tint = MaterialTheme.colorScheme.onErrorContainer,
                                modifier = Modifier.size(18.dp),
                            )
                        }
                    }
                    Spacer(Modifier.size(12.dp))
                    Text(
                        text = "确认删除",
                        style = MaterialTheme.typography.titleMedium,
                        fontWeight = FontWeight.SemiBold,
                    )
                }
                Spacer(Modifier.size(16.dp))
                Surface(
                    shape = RoundedCornerShape(10.dp),
                    color = MaterialTheme.colorScheme.errorContainer,
                    modifier = Modifier.fillMaxWidth(),
                ) {
                    Text(
                        text = "将删除选中的 $count 项，不可恢复。",
                        style = MaterialTheme.typography.bodySmall,
                        color = MaterialTheme.colorScheme.onErrorContainer,
                        modifier = Modifier.padding(horizontal = 12.dp, vertical = 10.dp),
                    )
                }
                Spacer(Modifier.size(20.dp))
                Row(
                    modifier = Modifier.fillMaxWidth(),
                    horizontalArrangement = Arrangement.End,
                    verticalAlignment = Alignment.CenterVertically,
                ) {
                    TextButton(onClick = onDismiss) { Text("取消") }
                    Spacer(Modifier.size(8.dp))
                    Surface(
                        onClick = onConfirm,
                        shape = RoundedCornerShape(10.dp),
                        color = MaterialTheme.colorScheme.error,
                    ) {
                        Text(
                            text = "删除",
                            color = MaterialTheme.colorScheme.onError,
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

private fun formatSize(bytes: Long): String = when {
    bytes < 1024 -> "$bytes B"
    bytes < 1024 * 1024 -> "${bytes / 1024} KB"
    bytes < 1024 * 1024 * 1024 -> "${bytes / (1024 * 1024)} MB"
    else -> "${bytes / (1024 * 1024 * 1024)} GB"
}
