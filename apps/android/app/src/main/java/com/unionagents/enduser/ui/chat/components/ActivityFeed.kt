package com.unionagents.enduser.ui.chat.components

import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.CheckCircle
import androidx.compose.material.icons.filled.PlayArrow
import androidx.compose.material.icons.filled.Psychology
import androidx.compose.material.icons.filled.Warning
import androidx.compose.material3.Icon
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Surface
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.remember
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.alpha
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.unit.dp
import java.text.SimpleDateFormat
import java.util.Date
import java.util.Locale

/**
 * 活动事件流（对齐 web `agent-activity-status` 渲染）：
 * 紧凑行展示 model / tool / run / waiting / warning 等中间过程事件，
 * 每行图标 + label + detail + 时间。仅流式期间展示（落定消息不快照 activityEvents）。
 */
@Composable
fun ActivityFeed(
    events: List<ActivityEvent>,
    modifier: Modifier = Modifier,
) {
    if (events.isEmpty()) return
    val timeFmt = remember { SimpleDateFormat("HH:mm", Locale.getDefault()) }
    Column(
        modifier = modifier.fillMaxWidth(),
        verticalArrangement = Arrangement.spacedBy(4.dp),
    ) {
        events.forEachIndexed { idx, ev ->
            ActivityEventRow(ev, timeFmt, key = idx)
        }
    }
}

@Composable
private fun ActivityEventRow(
    ev: ActivityEvent,
    timeFmt: SimpleDateFormat,
    key: Int,
) {
    val tint = when {
        ev.status == "error" -> MaterialTheme.colorScheme.error
        ev.kind == "warning" -> MaterialTheme.colorScheme.error
        ev.status == "done" -> MaterialTheme.colorScheme.onSurfaceVariant
        else -> MaterialTheme.colorScheme.primary
    }
    Surface(
        color = MaterialTheme.colorScheme.surfaceVariant.copy(alpha = 0.5f),
        shape = RoundedCornerShape(10.dp),
        modifier = Modifier.fillMaxWidth(),
    ) {
        Row(
            modifier = Modifier.padding(horizontal = 10.dp, vertical = 6.dp),
            verticalAlignment = Alignment.CenterVertically,
        ) {
            ActivityEventIcon(ev, tint, Modifier.size(14.dp))
            Spacer(Modifier.width(8.dp))
            Text(
                text = ev.label,
                style = MaterialTheme.typography.labelSmall,
                color = MaterialTheme.colorScheme.onSurfaceVariant,
                modifier = Modifier.alpha(if (ev.status == "waiting") 0.85f else 1f),
            )
            if (!ev.detail.isNullOrBlank()) {
                Spacer(Modifier.width(6.dp))
                Text(
                    text = ev.detail,
                    style = MaterialTheme.typography.labelSmall,
                    color = MaterialTheme.colorScheme.onSurfaceVariant.copy(alpha = 0.7f),
                )
            }
            Spacer(Modifier.width(8.dp))
            Text(
                text = timeFmt.format(Date(ev.ts * 1000)),
                style = MaterialTheme.typography.labelSmall,
                color = MaterialTheme.colorScheme.onSurfaceVariant.copy(alpha = 0.6f),
            )
        }
    }
}

@Composable
private fun ActivityEventIcon(ev: ActivityEvent, tint: Color, modifier: Modifier = Modifier) {
    when {
        ev.kind == "model" -> Icon(Icons.Filled.Psychology, null, modifier, tint)
        ev.kind == "tool" && ev.status == "done" -> Icon(Icons.Filled.CheckCircle, null, modifier, tint)
        ev.kind == "tool" && ev.status == "waiting" -> SpinnerDot(modifier, tint)
        ev.kind == "run" -> Icon(Icons.Filled.PlayArrow, null, modifier, tint)
        ev.kind == "warning" -> Icon(Icons.Filled.Warning, null, modifier, tint)
        ev.kind == "waiting" -> SpinnerDot(modifier, tint)
        else -> Icon(Icons.Filled.CheckCircle, null, modifier, tint)
    }
}

@Composable
private fun SpinnerDot(modifier: Modifier = Modifier, tint: Color) {
    Surface(
        color = tint,
        shape = CircleShape,
        modifier = modifier.size(8.dp),
    ) {}
}
