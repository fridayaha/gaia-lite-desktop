package com.unionagents.enduser.ui.mine

import androidx.compose.foundation.background
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.AccountCircle
import androidx.compose.material.icons.filled.Badge
import androidx.compose.material3.Icon
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Surface
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.remember
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.layout.ContentScale
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.unit.dp
import androidx.hilt.navigation.compose.hiltViewModel
import androidx.lifecycle.compose.collectAsStateWithLifecycle
import coil.compose.AsyncImage

/**
 * 「编辑个人资料」入口（从 MineScreen 「编辑个人资料」按钮进）。
 * 头像 + 真实姓名两行，点进去各自开详情页编辑。
 */
@Composable
fun ProfileEditScreen(
    onBack: () -> Unit,
    onEditAvatar: () -> Unit,
    onEditField: (field: String) -> Unit,
    viewModel: MineViewModel = hiltViewModel(),
) {
    val ui by viewModel.ui.collectAsStateWithLifecycle()
    val context = LocalContext.current

    LaunchedEffect(ui.profileError) {
        ui.profileError?.let { msg ->
            android.widget.Toast.makeText(context, msg, android.widget.Toast.LENGTH_SHORT).show()
            viewModel.clearProfileError()
        }
    }

    SettingsScaffold(title = "编辑个人资料", onBack = onBack) {
        SettingsCard {
            AvatarRow(
                avatarUrl = ui.user?.avatarUrl,
                onClick = onEditAvatar,
                showDivider = true,
            )
            SettingsRow(
                title = "真实姓名",
                onClick = { onEditField("real_name") },
                leading = {
                    Icon(
                        Icons.Filled.Badge,
                        contentDescription = null,
                        tint = MaterialTheme.colorScheme.onSurfaceVariant,
                        modifier = Modifier.size(22.dp),
                    )
                },
                trailing = {
                    Text(
                        text = ui.user?.realName?.ifBlank { null } ?: "未设置",
                        style = MaterialTheme.typography.bodyMedium,
                        color = MaterialTheme.colorScheme.onSurfaceVariant,
                    )
                },
                showDivider = false,
            )
        }
    }
}

@Composable
private fun AvatarRow(
    avatarUrl: String?,
    onClick: () -> Unit,
    showDivider: Boolean,
) {
    Box {
        SettingsRow(
            title = "头像",
            onClick = onClick,
            showDivider = showDivider,
            trailing = {
                Row(verticalAlignment = Alignment.CenterVertically) {
                    val absoluteAvatar = remember(avatarUrl) { avatarAbsoluteUrl(avatarUrl) }
                    if (absoluteAvatar != null) {
                        AsyncImage(
                            model = absoluteAvatar,
                            contentDescription = null,
                            contentScale = ContentScale.Crop,
                            modifier = Modifier
                                .size(36.dp)
                                .clip(CircleShape),
                        )
                    } else {
                        Surface(
                            shape = CircleShape,
                            color = MaterialTheme.colorScheme.primary,
                            modifier = Modifier.size(36.dp),
                        ) {
                            Box(contentAlignment = Alignment.Center) {
                                Icon(
                                    Icons.Filled.AccountCircle,
                                    contentDescription = null,
                                    tint = MaterialTheme.colorScheme.onPrimary,
                                    modifier = Modifier.size(22.dp),
                                )
                            }
                        }
                    }
                    Spacer(Modifier.size(8.dp))
                    Text(
                        text = "点击更换",
                        style = MaterialTheme.typography.bodySmall,
                        color = MaterialTheme.colorScheme.onSurfaceVariant,
                    )
                    Spacer(Modifier.size(4.dp))
                }
            },
        )
    }
}
