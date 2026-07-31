package com.unionagents.enduser.ui.workspace

import android.content.Intent
import android.graphics.Bitmap
import android.graphics.pdf.PdfRenderer
import android.os.ParcelFileDescriptor
import android.webkit.WebView
import android.widget.Toast
import androidx.activity.compose.BackHandler
import androidx.compose.foundation.Image
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.verticalScroll
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.automirrored.filled.ArrowBack
import androidx.compose.material3.CircularProgressIndicator
import androidx.compose.material3.ExperimentalMaterial3Api
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Scaffold
import androidx.compose.material3.Surface
import androidx.compose.material3.Text
import androidx.compose.material3.TopAppBar
import androidx.compose.material3.TopAppBarDefaults
import androidx.compose.runtime.Composable
import androidx.compose.runtime.DisposableEffect
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateListOf
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.asImageBitmap
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import androidx.compose.ui.viewinterop.AndroidView
import androidx.core.content.FileProvider
import coil.compose.AsyncImage
import com.unionagents.enduser.net.dto.WorkspaceFileContent
import com.unionagents.enduser.repo.WorkspaceRepository
import com.unionagents.enduser.ui.chat.components.MarkdownText
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext
import java.io.File

@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun FilePreviewScreen(
    agentId: String,
    path: String,
    onBack: () -> Unit,
    workspaceRepository: WorkspaceRepository,
    modifier: Modifier = Modifier,
) {
    BackHandler { onBack() }

    val context = LocalContext.current
    val scope = rememberCoroutineScope()

    var content by remember { mutableStateOf<WorkspaceFileContent?>(null) }
    var rawBytes by remember { mutableStateOf<ByteArray?>(null) }
    var loading by remember { mutableStateOf(true) }
    var downloadingBinary by remember { mutableStateOf(false) }
    var error by remember { mutableStateOf<String?>(null) }

    LaunchedEffect(agentId, path) {
        loading = true
        downloadingBinary = false
        error = null
        content = null
        rawBytes = null
        try {
            val c = workspaceRepository.readFile(agentId, path)
            content = c
            val type = c.previewType()
            if (type == PreviewType.Pdf || type == PreviewType.Office) {
                downloadingBinary = true
                rawBytes = workspaceRepository.downloadFile(agentId, path)
                downloadingBinary = false
            }
        } catch (e: Throwable) {
            error = e.message ?: "加载失败"
        } finally {
            loading = false
            downloadingBinary = false
        }
    }

    Scaffold(
        modifier = modifier,
        topBar = {
            TopAppBar(
                title = {
                    Text(
                        text = content?.name ?: path.substringAfterLast('/'),
                        fontWeight = FontWeight.SemiBold,
                    )
                },
                navigationIcon = {
                    IconButton(onClick = onBack) {
                        Icon(
                            imageVector = Icons.AutoMirrored.Filled.ArrowBack,
                            contentDescription = "返回",
                        )
                    }
                },
                colors = TopAppBarDefaults.topAppBarColors(
                    containerColor = MaterialTheme.colorScheme.background,
                    titleContentColor = MaterialTheme.colorScheme.onBackground,
                ),
            )
        },
    ) { padding ->
        Box(modifier = Modifier.fillMaxSize().padding(padding)) {
            when {
                loading -> Box(modifier = Modifier.fillMaxSize(), contentAlignment = Alignment.Center) {
                    CircularProgressIndicator()
                }
                error != null -> Column(
                    modifier = Modifier.fillMaxSize().padding(24.dp),
                    horizontalAlignment = Alignment.CenterHorizontally,
                    verticalArrangement = Arrangement.Center,
                ) {
                    Text("预览失败", style = MaterialTheme.typography.titleMedium, fontWeight = FontWeight.SemiBold)
                    Text(
                        error ?: "",
                        style = MaterialTheme.typography.bodySmall,
                        color = MaterialTheme.colorScheme.onSurfaceVariant,
                        modifier = Modifier.padding(top = 4.dp),
                    )
                }
                content != null -> FilePreviewBody(
                    content = content!!,
                    rawBytes = rawBytes,
                    downloadingBinary = downloadingBinary,
                    onOpenWith = { bytes, fileName ->
                        openWithExternalApp(context, bytes, fileName)
                    },
                )
            }
        }
    }
}

@Composable
private fun FilePreviewBody(
    content: WorkspaceFileContent,
    rawBytes: ByteArray?,
    downloadingBinary: Boolean,
    onOpenWith: (ByteArray, String) -> Unit,
) {
    when (content.previewType()) {
        PreviewType.Image -> Box(modifier = Modifier.fillMaxSize().padding(8.dp), contentAlignment = Alignment.Center) {
            val data = content.contentB64 ?: content.content ?: ""
            val imageBytes = runCatching {
                android.util.Base64.decode(data, android.util.Base64.DEFAULT)
            }.getOrNull()
            if (imageBytes != null) {
                AsyncImage(
                    model = imageBytes,
                    contentDescription = content.name,
                    modifier = Modifier.fillMaxWidth(),
                )
            } else {
                Text("无法预览此图片", color = MaterialTheme.colorScheme.onSurfaceVariant)
            }
        }
        PreviewType.Markdown -> Column(
            modifier = Modifier
                .fillMaxSize()
                .padding(12.dp)
                .verticalScroll(rememberScrollState()),
        ) {
            TruncatedNotice(content)
            MarkdownText(markdown = content.decodeTextContent())
        }
        PreviewType.Html -> HtmlPreview(html = content.decodeTextContent())
        PreviewType.Text -> Column(
            modifier = Modifier
                .fillMaxSize()
                .padding(12.dp)
                .verticalScroll(rememberScrollState()),
        ) {
            TruncatedNotice(content)
            Text(
                text = content.decodeTextContent(),
                style = MaterialTheme.typography.bodySmall,
                color = MaterialTheme.colorScheme.onSurface,
            )
        }
        PreviewType.Pdf -> when {
            downloadingBinary -> Box(modifier = Modifier.fillMaxSize(), contentAlignment = Alignment.Center) {
                CircularProgressIndicator()
            }
            rawBytes != null -> PdfPreview(bytes = rawBytes)
            else -> UnsupportedFile(content)
        }
        PreviewType.Office -> when {
            downloadingBinary -> Box(modifier = Modifier.fillMaxSize(), contentAlignment = Alignment.Center) {
                CircularProgressIndicator()
            }
            rawBytes != null -> ExternalOpenPrompt(
                content = content,
                bytes = rawBytes,
                onOpenWith = onOpenWith,
            )
            else -> UnsupportedFile(content)
        }
        PreviewType.Unknown -> UnsupportedFile(content)
    }
}

@Composable
private fun TruncatedNotice(content: WorkspaceFileContent) {
    if (content.truncated) {
        Text(
            text = "文件较大，仅显示前 ${formatSize(content.maxBytes)}",
            style = MaterialTheme.typography.labelSmall,
            color = MaterialTheme.colorScheme.tertiary,
            modifier = Modifier.padding(bottom = 8.dp),
        )
    }
}

@Composable
private fun HtmlPreview(html: String) {
    val linkColor = MaterialTheme.colorScheme.primary
    AndroidView(
        factory = { ctx ->
            WebView(ctx).apply {
                settings.javaScriptEnabled = false
                settings.allowFileAccess = false
                settings.allowContentAccess = false
                setBackgroundColor(android.graphics.Color.TRANSPARENT)
            }
        },
        update = { webView ->
            webView.loadDataWithBaseURL(null, html, "text/html", "UTF-8", null)
        },
        modifier = Modifier.fillMaxSize(),
    )
}

@Composable
private fun PdfPreview(bytes: ByteArray) {
    val context = LocalContext.current
    val scope = rememberCoroutineScope()
    val pages = remember { mutableStateListOf<androidx.compose.ui.graphics.ImageBitmap?>() }

    DisposableEffect(bytes) {
        pages.clear()
        val file = File(context.cacheDir, "preview-${System.currentTimeMillis()}.pdf")
        file.writeBytes(bytes)
        var renderer: PdfRenderer? = null
        val job = scope.launch(Dispatchers.IO) {
            try {
                val fd = ParcelFileDescriptor.open(file, ParcelFileDescriptor.MODE_READ_ONLY)
                renderer = PdfRenderer(fd)
                val r = renderer!!
                val page0 = r.openPage(0)
                val pageWidth = page0.width
                page0.close()
                val targetWidth = context.resources.displayMetrics.widthPixels.toFloat() - 32.dp.value *
                    context.resources.displayMetrics.density
                val scale = (targetWidth / pageWidth.coerceAtLeast(1)).coerceAtLeast(0.1f)
                repeat(r.pageCount) { index ->
                    val page = r.openPage(index)
                    val width = (page.width * scale).toInt().coerceAtLeast(1)
                    val height = (page.height * scale).toInt().coerceAtLeast(1)
                    val bitmap = Bitmap.createBitmap(width, height, Bitmap.Config.ARGB_8888)
                    page.render(bitmap, null, null, PdfRenderer.Page.RENDER_MODE_FOR_DISPLAY)
                    page.close()
                    withContext(Dispatchers.Main) {
                        pages.add(bitmap.asImageBitmap())
                    }
                }
            } finally {
                renderer?.close()
                file.delete()
            }
        }
        onDispose { job.cancel() }
    }

    LazyColumn(
        modifier = Modifier.fillMaxSize().padding(12.dp),
        verticalArrangement = Arrangement.spacedBy(12.dp),
    ) {
        items(pages.size) { index ->
            val page = pages.getOrNull(index)
            if (page != null) {
                Image(
                    bitmap = page,
                    contentDescription = "PDF 第 ${index + 1} 页",
                    modifier = Modifier.fillMaxWidth(),
                )
            } else {
                Box(
                    modifier = Modifier.fillMaxWidth().height(200.dp),
                    contentAlignment = Alignment.Center,
                ) {
                    CircularProgressIndicator()
                }
            }
        }
    }
}

@Composable
private fun ExternalOpenPrompt(
    content: WorkspaceFileContent,
    bytes: ByteArray,
    onOpenWith: (ByteArray, String) -> Unit,
) {
    Column(
        modifier = Modifier.fillMaxSize().padding(24.dp),
        horizontalAlignment = Alignment.CenterHorizontally,
        verticalArrangement = Arrangement.Center,
    ) {
        Text(
            text = content.name,
            style = MaterialTheme.typography.titleMedium,
            fontWeight = FontWeight.SemiBold,
        )
        Text(
            text = "${formatSize(content.size)} · ${content.name.fileExtension().uppercase()}",
            style = MaterialTheme.typography.bodySmall,
            color = MaterialTheme.colorScheme.onSurfaceVariant,
            modifier = Modifier.padding(top = 4.dp),
        )
        Text(
            text = "该格式暂不支持应用内预览，可使用其他应用打开",
            style = MaterialTheme.typography.bodySmall,
            color = MaterialTheme.colorScheme.onSurfaceVariant,
            modifier = Modifier.padding(top = 12.dp),
        )
        Surface(
            onClick = { onOpenWith(bytes, content.name) },
            shape = MaterialTheme.shapes.medium,
            color = MaterialTheme.colorScheme.primary,
            modifier = Modifier.padding(top = 20.dp),
        ) {
            Text(
                text = "用其他应用打开",
                color = MaterialTheme.colorScheme.onPrimary,
                fontWeight = FontWeight.Medium,
                modifier = Modifier.padding(horizontal = 24.dp, vertical = 12.dp),
            )
        }
    }
}

@Composable
private fun UnsupportedFile(content: WorkspaceFileContent) {
    Column(
        modifier = Modifier.fillMaxSize().padding(24.dp),
        horizontalAlignment = Alignment.CenterHorizontally,
        verticalArrangement = Arrangement.Center,
    ) {
        Text("不支持预览此文件", color = MaterialTheme.colorScheme.onSurfaceVariant)
        Text(
            text = "${content.name} · ${formatSize(content.size)}",
            style = MaterialTheme.typography.bodySmall,
            color = MaterialTheme.colorScheme.onSurfaceVariant,
            modifier = Modifier.padding(top = 4.dp),
        )
    }
}

private fun openWithExternalApp(context: android.content.Context, bytes: ByteArray, fileName: String) {
    val ext = fileName.fileExtension()
    val cacheFile = File(context.cacheDir, "share-${System.currentTimeMillis()}.$ext").apply {
        writeBytes(bytes)
    }
    val authority = "${context.packageName}.fileprovider"
    val uri = FileProvider.getUriForFile(context, authority, cacheFile)
    val mime = fileName.mimeTypeForExtension()
    val intent = Intent(Intent.ACTION_VIEW).apply {
        setDataAndType(uri, mime)
        addFlags(Intent.FLAG_GRANT_READ_URI_PERMISSION)
        addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)
    }
    val chooser = Intent.createChooser(intent, "打开文件")
    chooser.addFlags(Intent.FLAG_GRANT_READ_URI_PERMISSION)
    try {
        context.startActivity(chooser)
    } catch (_: Throwable) {
        Toast.makeText(context, "没有可打开此文件的应用", Toast.LENGTH_SHORT).show()
    }
}

private fun formatSize(bytes: Long): String = when {
    bytes < 1024 -> "$bytes B"
    bytes < 1024 * 1024 -> "${bytes / 1024} KB"
    bytes < 1024 * 1024 * 1024 -> "${bytes / (1024 * 1024)} MB"
    else -> "${bytes / (1024 * 1024 * 1024)} GB"
}
