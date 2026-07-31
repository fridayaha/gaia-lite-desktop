package com.unionagents.enduser.ui.chat.components

import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Surface
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.ui.Modifier
import androidx.compose.ui.unit.dp
import kotlin.math.roundToInt

/**
 * 回复用量元数据：model · 耗时 · tokens（对齐 web StatusCard）。
 * 任一字段缺失即跳过分隔点；全为空时整卡隐藏。
 */
@Composable
fun StatusCard(
    model: String?,
    durationSec: Double?,
    tokens: Int?,
) {
    val hasModel = !model.isNullOrBlank()
    val hasDur = durationSec != null
    val hasTok = tokens != null && tokens > 0
    if (!hasModel && !hasDur && !hasTok) return

    Surface(
        color = MaterialTheme.colorScheme.surfaceVariant,
        shape = RoundedCornerShape(50),
        modifier = Modifier.fillMaxWidth(),
    ) {
        Row(
            modifier = Modifier.padding(horizontal = 12.dp, vertical = 6.dp),
            horizontalArrangement = Arrangement.spacedBy(4.dp),
        ) {
            if (hasModel) {
                Text(
                    text = model!!,
                    style = MaterialTheme.typography.labelSmall,
                    color = MaterialTheme.colorScheme.onSurfaceVariant,
                )
            }
            if (hasModel && hasDur) {
                Text(
                    text = " · ",
                    style = MaterialTheme.typography.labelSmall,
                    color = MaterialTheme.colorScheme.onSurfaceVariant,
                )
            }
            if (hasDur) {
                Text(
                    text = fmtDur(durationSec!!),
                    style = MaterialTheme.typography.labelSmall,
                    color = MaterialTheme.colorScheme.onSurfaceVariant,
                )
            }
            if ((hasModel || hasDur) && hasTok) {
                Text(
                    text = " · ",
                    style = MaterialTheme.typography.labelSmall,
                    color = MaterialTheme.colorScheme.onSurfaceVariant,
                )
            }
            if (hasTok) {
                Text(
                    text = "${"%,d".format(tokens)} tokens",
                    style = MaterialTheme.typography.labelSmall,
                    color = MaterialTheme.colorScheme.onSurfaceVariant,
                )
            }
        }
    }
}

private fun fmtDur(s: Double): String {
    if (s < 60.0) return String.format("%.1f秒", s)
    val m = (s / 60.0).toInt()
    val rs = (s % 60.0).roundToInt()
    return "${m}分${rs}秒"
}
