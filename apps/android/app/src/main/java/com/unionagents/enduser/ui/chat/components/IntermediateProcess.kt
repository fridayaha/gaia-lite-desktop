package com.unionagents.enduser.ui.chat.components

import androidx.compose.animation.core.animateFloatAsState
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.automirrored.filled.KeyboardArrowRight
import androidx.compose.material.icons.filled.CheckCircle
import androidx.compose.material.icons.filled.Warning
import androidx.compose.material3.CircularProgressIndicator
import androidx.compose.material3.Icon
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Surface
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.rotate
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import com.unionagents.enduser.net.dto.ToolCallState

/**
 * 回复上方的中间过程收起栏（对齐 web `agent-activity-group`）：
 * 外层 chevron + 摘要（已思考 · N 个工具 · M 条过程）→ 展开后 body 内平铺
 * ActivityFeed + ThinkingCard + 所有 ToolCard（done + running 不再分子折叠）。
 *
 * - defaultExpanded：流式期间 true（实时看进度），落定消息 false（默认折叠，点击展开）
 * - 收起态下下方追加一行"当前执行项"预览（正在跑的工具优先，否则最近一条事件），
 *   让用户在折叠态也能看到正在做什么，无需展开。
 */
@Composable
fun IntermediateProcess(
    thinking: String?,
    thinkingStatus: ThinkingStatus,
    toolCalls: List<ToolCallState>,
    activityEvents: List<ActivityEvent> = emptyList(),
    defaultExpanded: Boolean = true,
    modifier: Modifier = Modifier,
) {
    var expanded by remember { mutableStateOf(defaultExpanded) }

    val summaryParts = mutableListOf<String>()
    if (!thinking.isNullOrBlank()) summaryParts.add("已思考")
    val toolCount = toolCalls.size
    if (toolCount > 0) summaryParts.add("$toolCount 个工具")
    if (activityEvents.isNotEmpty()) summaryParts.add("${activityEvents.size} 条过程")
    val summary = if (summaryParts.isEmpty()) "中间过程" else summaryParts.joinToString(" · ")

    val chevronRotation by animateFloatAsState(
        targetValue = if (expanded) 90f else 0f,
        label = "process-chevron",
    )

    Surface(
        color = MaterialTheme.colorScheme.surfaceVariant,
        shape = RoundedCornerShape(12.dp),
        modifier = modifier.fillMaxWidth(0.85f),
    ) {
        Column(modifier = Modifier.fillMaxWidth()) {
            Row(
                verticalAlignment = Alignment.CenterVertically,
                modifier = Modifier
                    .fillMaxWidth()
                    .clickable { expanded = !expanded }
                    .padding(horizontal = 12.dp, vertical = 8.dp),
            ) {
                Icon(
                    Icons.AutoMirrored.Filled.KeyboardArrowRight,
                    contentDescription = null,
                    modifier = Modifier.size(14.dp).rotate(chevronRotation),
                    tint = MaterialTheme.colorScheme.onSurfaceVariant,
                )
                Spacer(Modifier.size(6.dp))
                Text(
                    text = summary,
                    style = MaterialTheme.typography.labelMedium,
                    color = MaterialTheme.colorScheme.onSurfaceVariant,
                    modifier = Modifier.weight(1f),
                )
            }
            if (!expanded) {
                CollapsedPreview(toolCalls = toolCalls, activityEvents = activityEvents)
            }
            if (expanded) {
                Column(
                    modifier = Modifier
                        .fillMaxWidth()
                        .padding(horizontal = 12.dp, vertical = 8.dp),
                    verticalArrangement = Arrangement.spacedBy(6.dp),
                ) {
                    if (activityEvents.isNotEmpty()) {
                        ActivityFeed(events = activityEvents)
                    }
                    if (!thinking.isNullOrBlank()) {
                        ThinkingCard(text = thinking, status = thinkingStatus)
                    }
                    toolCalls.forEach { t ->
                        ToolCard(
                            name = t.name,
                            preview = t.preview,
                            completed = t.completed,
                            error = t.error,
                            result = t.result,
                        )
                    }
                }
            }
        }
    }
}

/**
 * 收起态下的"当前执行项"预览行：优先展示正在跑的工具（spinner + 工具名 + preview），
 * 否则展示最近一条活动事件（按 status 选 done/error 图标或 waiting 的 spinner）。
 */
@Composable
private fun CollapsedPreview(
    toolCalls: List<ToolCallState>,
    activityEvents: List<ActivityEvent>,
) {
    val runningTool = toolCalls.lastOrNull { !it.completed }
    val latestEvent = activityEvents.lastOrNull()
    val previewLabel = when {
        runningTool != null -> runningTool.name +
            (runningTool.preview?.takeIf { it.isNotBlank() }?.let { " · ${truncatePreview(it)}" } ?: "")
        latestEvent != null -> latestEvent.label +
            (latestEvent.detail?.takeIf { it.isNotBlank() }?.let { " · $it" } ?: "")
        else -> null
    } ?: return

    val isError = latestEvent?.status == "error" && runningTool == null
    val isWaiting = runningTool != null || latestEvent?.status == "waiting"
    val tint = if (isError) MaterialTheme.colorScheme.error else MaterialTheme.colorScheme.onSurfaceVariant

    Row(
        verticalAlignment = Alignment.CenterVertically,
        modifier = Modifier
            .fillMaxWidth()
            .padding(horizontal = 12.dp, vertical = 6.dp),
    ) {
        when {
            isWaiting && !isError -> CircularProgressIndicator(
                modifier = Modifier.size(14.dp),
                strokeWidth = 1.5.dp,
            )
            isError -> Icon(Icons.Filled.Warning, null, Modifier.size(14.dp), tint)
            else -> Icon(Icons.Filled.CheckCircle, null, Modifier.size(14.dp), tint)
        }
        Spacer(Modifier.size(6.dp))
        Text(
            text = previewLabel,
            style = MaterialTheme.typography.labelSmall,
            color = tint,
            maxLines = 1,
            overflow = TextOverflow.Ellipsis,
        )
    }
}
