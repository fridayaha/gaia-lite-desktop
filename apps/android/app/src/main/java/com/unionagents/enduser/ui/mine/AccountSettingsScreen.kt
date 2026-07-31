package com.unionagents.enduser.ui.mine

import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.AccountCircle
import androidx.compose.material.icons.filled.Badge
import androidx.compose.material.icons.filled.Mail
import androidx.compose.material.icons.filled.MarkEmailRead
import androidx.compose.material.icons.filled.MarkEmailUnread
import androidx.compose.material.icons.filled.Phone
import androidx.compose.material.icons.filled.PhoneIphone
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
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.layout.ContentScale
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.unit.dp
import androidx.hilt.navigation.compose.hiltViewModel
import androidx.lifecycle.compose.collectAsStateWithLifecycle
import coil.compose.AsyncImage

/**
 * 「账号设置」入口（从 SettingsScreen 「账号设置」行进）。
 * 头像 / 真实姓名 / 邮箱 / 手机 各自点进去详情页编辑；邮箱/手机未认证时显示「认证」按钮（渠道开启时）。
 */
@Composable
fun AccountSettingsScreen(
    onBack: () -> Unit,
    onEditAvatar: () -> Unit,
    onEditField: (field: String) -> Unit,
    onVerifyContact: (channel: String) -> Unit,
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

    val user = ui.user
    val email = user?.email?.ifBlank { null }
    val phone = user?.phone?.ifBlank { null }
    val emailVerified = user?.emailVerified == true
    val phoneVerified = user?.phoneVerified == true

    SettingsScaffold(title = "账号设置", onBack = onBack) {
        // ── 头像 + 真实姓名 ──
        SettingsCard {
            AvatarRow(
                avatarUrl = user?.avatarUrl,
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
                        text = user?.realName?.ifBlank { null } ?: "未设置",
                        style = MaterialTheme.typography.bodyMedium,
                        color = MaterialTheme.colorScheme.onSurfaceVariant,
                    )
                },
                showDivider = false,
            )
        }

        // ── 邮箱 + 认证邮箱 ──
        SettingsCard {
            SettingsRow(
                title = "邮箱",
                subtitle = email ?: "未设置",
                onClick = { onEditField("email") },
                leading = {
                    Icon(
                        Icons.Filled.Mail,
                        contentDescription = null,
                        tint = MaterialTheme.colorScheme.onSurfaceVariant,
                        modifier = Modifier.size(22.dp),
                    )
                },
                trailing = {
                    VerificationStatusPill(
                        value = email,
                        verified = emailVerified,
                        canVerify = ui.emailChannelEnabled && email != null && !emailVerified,
                        onVerify = { onVerifyContact("email") },
                    )
                },
                showDivider = false,
            )
        }

        // ── 手机 + 认证手机 ──
        SettingsCard {
            SettingsRow(
                title = "手机",
                subtitle = phone ?: "未设置",
                onClick = { onEditField("phone") },
                leading = {
                    Icon(
                        Icons.Filled.PhoneIphone,
                        contentDescription = null,
                        tint = MaterialTheme.colorScheme.onSurfaceVariant,
                        modifier = Modifier.size(22.dp),
                    )
                },
                trailing = {
                    VerificationStatusPill(
                        value = phone,
                        verified = phoneVerified,
                        canVerify = ui.smsChannelEnabled && phone != null && !phoneVerified,
                        onVerify = { onVerifyContact("phone") },
                    )
                },
                showDivider = false,
            )
        }

        Spacer(Modifier.size(8.dp))
        Text(
            text = "修改邮箱或手机后，认证状态会重置，需重新认证。",
            style = MaterialTheme.typography.bodySmall,
            color = MaterialTheme.colorScheme.onSurfaceVariant,
            modifier = Modifier.padding(horizontal = 8.dp),
        )
    }
}

@Composable
private fun AvatarRow(
    avatarUrl: String?,
    onClick: () -> Unit,
    showDivider: Boolean,
) {
    SettingsRow(
        title = "头像",
        onClick = onClick,
        showDivider = showDivider,
        trailing = {
            androidx.compose.foundation.layout.Row(verticalAlignment = Alignment.CenterVertically) {
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
                        androidx.compose.foundation.layout.Box(
                            contentAlignment = Alignment.Center,
                        ) {
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

/**
 * 联系方式右侧的状态：未设置 / 已认证 / 未认证（可点认证按钮）。
 */
@Composable
private fun VerificationStatusPill(
    value: String?,
    verified: Boolean,
    canVerify: Boolean,
    onVerify: () -> Unit,
) {
    if (value == null) {
        Text(
            text = "未设置",
            style = MaterialTheme.typography.bodySmall,
            color = MaterialTheme.colorScheme.onSurfaceVariant,
        )
        return
    }
    if (verified) {
        androidx.compose.foundation.layout.Row(verticalAlignment = Alignment.CenterVertically) {
            Icon(
                Icons.Filled.MarkEmailRead,
                contentDescription = null,
                tint = MaterialTheme.colorScheme.primary,
                modifier = Modifier.size(16.dp),
            )
            Spacer(Modifier.size(4.dp))
            Text(
                text = "已认证",
                style = MaterialTheme.typography.bodySmall,
                color = MaterialTheme.colorScheme.primary,
            )
        }
    } else if (canVerify) {
        Surface(
            onClick = onVerify,
            shape = androidx.compose.foundation.shape.RoundedCornerShape(50),
            color = MaterialTheme.colorScheme.primaryContainer,
        ) {
            androidx.compose.foundation.layout.Row(
                modifier = Modifier.padding(horizontal = 10.dp, vertical = 4.dp),
                verticalAlignment = Alignment.CenterVertically,
            ) {
                Icon(
                    Icons.Filled.MarkEmailUnread,
                    contentDescription = null,
                    tint = MaterialTheme.colorScheme.primary,
                    modifier = Modifier.size(14.dp),
                )
                Spacer(Modifier.size(4.dp))
                Text(
                    text = "认证",
                    style = MaterialTheme.typography.labelMedium,
                    color = MaterialTheme.colorScheme.primary,
                )
            }
        }
    } else {
        Text(
            text = "未认证",
            style = MaterialTheme.typography.bodySmall,
            color = MaterialTheme.colorScheme.onSurfaceVariant,
        )
    }
}
