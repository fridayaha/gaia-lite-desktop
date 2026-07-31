package com.unionagents.enduser.ui.chat.components

import androidx.compose.animation.AnimatedVisibility
import androidx.compose.animation.fadeIn
import androidx.compose.animation.fadeOut
import androidx.compose.animation.slideInVertically
import androidx.compose.animation.slideOutVertically
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.Check
import androidx.compose.material.icons.filled.Close
import androidx.compose.material.icons.filled.Lock
import androidx.compose.material.icons.filled.Star
import androidx.compose.material.icons.filled.Warning
import androidx.compose.material3.Button
import androidx.compose.material3.Icon
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.Surface
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.vector.ImageVector
import androidx.compose.ui.res.stringResource
import androidx.compose.ui.text.font.FontFamily
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import com.unionagents.enduser.R

@Composable
fun ApprovalCard(
    command: String,
    description: String?,
    choices: List<String>,
    onSubmit: (String) -> Unit,
    submitting: Boolean,
    responded: Boolean = false,
    respondedChoice: String? = null,
) {
    AnimatedVisibility(
        visible = true,
        enter = fadeIn() + slideInVertically(initialOffsetY = { it / 2 }),
        exit = fadeOut() + slideOutVertically(targetOffsetY = { it / 2 }),
    ) {
        Surface(
            color = MaterialTheme.colorScheme.tertiaryContainer,
            shape = RoundedCornerShape(12.dp),
            modifier = Modifier.fillMaxWidth(),
        ) {
            Column(modifier = Modifier.padding(12.dp)) {
                Row(verticalAlignment = Alignment.CenterVertically) {
                    Icon(
                        Icons.Filled.Warning,
                        contentDescription = null,
                        modifier = Modifier.size(14.dp),
                        tint = MaterialTheme.colorScheme.onTertiaryContainer,
                    )
                    Spacer(Modifier.size(6.dp))
                    Text(
                        text = stringResource(R.string.approval_title),
                        style = MaterialTheme.typography.labelMedium,
                        color = MaterialTheme.colorScheme.onTertiaryContainer,
                        fontWeight = FontWeight.Bold,
                    )
                }
                if (!description.isNullOrBlank()) {
                    Text(
                        text = description,
                        style = MaterialTheme.typography.bodySmall,
                        color = MaterialTheme.colorScheme.onTertiaryContainer,
                        modifier = Modifier.padding(top = 6.dp),
                    )
                }
                if (command.isNotBlank()) {
                    Surface(
                        color = MaterialTheme.colorScheme.surface,
                        shape = RoundedCornerShape(6.dp),
                        modifier = Modifier.fillMaxWidth().padding(top = 6.dp),
                    ) {
                        Text(
                            text = command,
                            style = MaterialTheme.typography.labelSmall.copy(fontFamily = FontFamily.Monospace),
                            color = MaterialTheme.colorScheme.onSurface,
                            modifier = Modifier.padding(horizontal = 8.dp, vertical = 6.dp),
                        )
                    }
                }
                Spacer(Modifier.size(8.dp))
                if (responded) {
                    Text(
                        text = "已响应：${labelOf(respondedChoice ?: "")}",
                        style = MaterialTheme.typography.labelMedium,
                        color = MaterialTheme.colorScheme.primary,
                        fontWeight = FontWeight.Medium,
                    )
                } else {
                    Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                        choices.forEach { choice ->
                            val isDeny = choice == "deny"
                            if (isDeny) {
                                OutlinedButton(
                                    onClick = { onSubmit(choice) },
                                    enabled = !submitting,
                                ) {
                                    Icon(iconOf(choice), contentDescription = null, modifier = Modifier.size(14.dp))
                                    Spacer(Modifier.size(4.dp))
                                    Text(labelOf(choice))
                                }
                            } else {
                                Button(
                                    onClick = { onSubmit(choice) },
                                    enabled = !submitting,
                                ) {
                                    Icon(iconOf(choice), contentDescription = null, modifier = Modifier.size(14.dp))
                                    Spacer(Modifier.size(4.dp))
                                    Text(labelOf(choice))
                                }
                            }
                        }
                    }
                }
            }
        }
    }
}

private fun labelOf(c: String): String = when (c) {
    "once" -> "仅本次"
    "session" -> "本会话"
    "always" -> "永久允许"
    "deny" -> "拒绝"
    else -> c
}

private fun iconOf(c: String): ImageVector = when (c) {
    "once" -> Icons.Filled.Check
    "session" -> Icons.Filled.Lock
    "always" -> Icons.Filled.Star
    "deny" -> Icons.Filled.Close
    else -> Icons.Filled.Check
}
