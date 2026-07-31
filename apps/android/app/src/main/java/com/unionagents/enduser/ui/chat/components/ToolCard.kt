package com.unionagents.enduser.ui.chat.components

import androidx.compose.animation.core.animateFloatAsState
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.heightIn
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.verticalScroll
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.Build
import androidx.compose.material.icons.filled.CheckCircle
import androidx.compose.material.icons.filled.Description
import androidx.compose.material.icons.filled.Edit
import androidx.compose.material.icons.filled.Error
import androidx.compose.material.icons.filled.Folder
import androidx.compose.material.icons.filled.PlayArrow
import androidx.compose.material.icons.filled.Search
import androidx.compose.material.icons.filled.Terminal
import androidx.compose.material.icons.filled.KeyboardArrowDown
import androidx.compose.material3.CircularProgressIndicator
import androidx.compose.material3.Icon
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Surface
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.rotate
import androidx.compose.ui.graphics.vector.ImageVector
import androidx.compose.ui.text.font.FontFamily
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp

@Composable
fun ToolCard(
    name: String,
    preview: String?,
    completed: Boolean,
    error: String?,
    result: String? = null,
) {
    var expanded by remember { mutableStateOf(false) }
    var showFullResult by remember { mutableStateOf(false) }
    val hasDetail = !result.isNullOrBlank()

    Surface(
        color = MaterialTheme.colorScheme.surfaceVariant,
        shape = RoundedCornerShape(12.dp),
        modifier = Modifier.fillMaxWidth(0.85f),
    ) {
        Column(modifier = Modifier.padding(horizontal = 10.dp, vertical = 6.dp)) {
            Row(
                verticalAlignment = Alignment.CenterVertically,
                modifier = if (hasDetail) Modifier.clickable { expanded = !expanded } else Modifier,
            ) {
                if (!completed) {
                    CircularProgressIndicator(
                        modifier = Modifier.size(14.dp),
                        strokeWidth = 1.5.dp,
                    )
                } else if (error.isNullOrBlank()) {
                    Icon(
                        Icons.Filled.CheckCircle,
                        contentDescription = null,
                        modifier = Modifier.size(14.dp),
                        tint = MaterialTheme.colorScheme.primary,
                    )
                } else {
                    Icon(
                        Icons.Filled.Error,
                        contentDescription = null,
                        modifier = Modifier.size(14.dp),
                        tint = MaterialTheme.colorScheme.error,
                    )
                }
                Spacer(Modifier.size(6.dp))
                Icon(
                    toolIconFor(name),
                    contentDescription = null,
                    modifier = Modifier.size(14.dp),
                    tint = MaterialTheme.colorScheme.onSurfaceVariant,
                )
                Spacer(Modifier.size(6.dp))
                Text(
                    text = name,
                    style = MaterialTheme.typography.labelSmall,
                    color = MaterialTheme.colorScheme.onSurfaceVariant,
                )
                if (!preview.isNullOrBlank()) {
                    Spacer(Modifier.size(6.dp))
                    Text(
                        text = truncatePreview(preview),
                        style = MaterialTheme.typography.labelSmall,
                        color = MaterialTheme.colorScheme.onSurface,
                        maxLines = 1,
                        overflow = TextOverflow.Ellipsis,
                        modifier = Modifier.weight(1f),
                    )
                }
                if (hasDetail) {
                    val rotation by animateFloatAsState(if (expanded) 90f else 0f, label = "arrow-rotation")
                    Icon(
                        Icons.Filled.KeyboardArrowDown,
                        contentDescription = null,
                        modifier = Modifier.size(14.dp).rotate(rotation),
                        tint = MaterialTheme.colorScheme.onSurfaceVariant,
                    )
                }
            }
            if (!completed) {
                // running：不再显示 LinearProgressIndicator，header 的转圈已足够
            }
            if (expanded && hasDetail) {
                val displayed = if (showFullResult) result!! else truncateResult(result!!)
                Surface(
                    color = MaterialTheme.colorScheme.surface,
                    shape = RoundedCornerShape(6.dp),
                    modifier = Modifier.fillMaxWidth().padding(top = 8.dp),
                ) {
                    Column(modifier = Modifier.padding(8.dp)) {
                        // 对齐 web .tool-card-result pre：max-height 240 + overflow-y auto + 等宽小字
                        // 长结果在卡内滚动而非撑高整张卡
                        Text(
                            text = displayed,
                            style = MaterialTheme.typography.labelSmall.copy(
                                fontFamily = FontFamily.Monospace,
                                fontSize = 11.sp,
                                lineHeight = 14.sp,
                            ),
                            color = MaterialTheme.colorScheme.onSurface,
                            modifier = Modifier
                                .fillMaxWidth()
                                .heightIn(max = 240.dp)
                                .verticalScroll(rememberScrollState()),
                        )
                        if (result.length > 800) {
                            TextButton(
                                onClick = { showFullResult = !showFullResult },
                                contentPadding = androidx.compose.foundation.layout.PaddingValues(0.dp),
                            ) {
                                Text(if (showFullResult) "收起" else "展开更多")
                            }
                        }
                    }
                }
            }
        }
    }
}

private fun toolIconFor(name: String): ImageVector {
    val n = name.lowercase()
    return when {
        n == "terminal" -> Icons.Filled.Terminal
        n == "execute_code" || n == "python" -> Icons.Filled.PlayArrow
        n == "write_file" || n == "edit_file" -> Icons.Filled.Edit
        n == "read_file" || n == "view_file" -> Icons.Filled.Description
        n == "web_search" || n == "search_web" -> Icons.Filled.Search
        n == "list_directory" || n == "listdir" -> Icons.Filled.Folder
        n == "patch" -> Icons.Filled.Edit
        else -> Icons.Filled.Build
    }
}

internal fun truncatePreview(p: String): String {
    if (p.length <= 120) return p
    val cut = p.take(120)
    val lastBreak = maxOf(cut.lastIndexOf(' '), cut.lastIndexOf('\n'), cut.lastIndexOf(';'))
    return (if (lastBreak > 40) cut.take(lastBreak) else cut) + "…"
}

internal fun truncateResult(r: String): String {
    if (r.length <= 800) return r
    val cut = r.take(800)
    val lastBreak = maxOf(cut.lastIndexOf('\n'), cut.lastIndexOf(". "), cut.lastIndexOf("。"))
    return (if (lastBreak > 400) cut.take(lastBreak) else cut) + "…"
}
