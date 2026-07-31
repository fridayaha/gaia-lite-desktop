package com.unionagents.enduser.ui.mine

import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.text.selection.SelectionContainer
import androidx.compose.foundation.verticalScroll
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.automirrored.filled.Logout
import androidx.compose.material.icons.filled.BugReport
import androidx.compose.material.icons.filled.Code
import androidx.compose.material.icons.filled.Info
import androidx.compose.material.icons.filled.ManageAccounts
import androidx.compose.material.icons.filled.Settings
import androidx.compose.material.icons.filled.SystemUpdate
import androidx.compose.material.icons.filled.Timeline
import androidx.compose.material3.AlertDialog
import androidx.compose.material3.Badge
import androidx.compose.material3.CircularProgressIndicator
import androidx.compose.material3.ExperimentalMaterial3Api
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Switch
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.platform.LocalClipboardManager
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.res.stringResource
import androidx.compose.ui.text.AnnotatedString
import androidx.compose.ui.unit.dp
import androidx.hilt.navigation.compose.hiltViewModel
import androidx.lifecycle.compose.collectAsStateWithLifecycle
import com.unionagents.enduser.R
import com.unionagents.enduser.sse.StreamProbe
import java.io.File

/**
 * 设置页 — 「我的」右上角齿轮进。账号设置 / 开发者模式 / 检查更新 / 版本 / 关于 / 切换账号 / 退出登录。
 * 后续要加新设置项时，按 SettingsCard + SettingsRow 风格追加。
 */
@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun SettingsScreen(
    onBack: () -> Unit,
    onOpenAccountSettings: () -> Unit,
    onSwitchAccount: () -> Unit,
    onAbout: () -> Unit,
    onVersionInfo: () -> Unit,
    onUpdate: () -> Unit,
    onLogout: () -> Unit,
    viewModel: MineViewModel = hiltViewModel(),
) {
    val ui by viewModel.ui.collectAsStateWithLifecycle()
    val context = LocalContext.current
    val clipboard = LocalClipboardManager.current
    var showCrashLog by remember { mutableStateOf(false) }
    val crashLog = remember { readCrashLog(context) }
    var showProbeLog by remember { mutableStateOf(false) }
    var probeLogVersion by remember { mutableStateOf(0) }
    val probeLog = remember(probeLogVersion) { StreamProbe.readLog() }

    SettingsScaffold(title = "设置", onBack = onBack) {
        // ── 账号设置 ──
        SettingsCard {
            SettingsRow(
                title = "账号设置",
                onClick = onOpenAccountSettings,
                leading = {
                    androidx.compose.material3.Icon(
                        Icons.Filled.ManageAccounts,
                        contentDescription = null,
                        tint = MaterialTheme.colorScheme.onSurfaceVariant,
                        modifier = Modifier.size(22.dp),
                    )
                },
                showDivider = false,
            )
        }

        // ── 开发者模式 / 检查更新 / 版本 / 关于 ──
        SettingsCard {
            SettingsRow(
                title = "开发者模式",
                onClick = { viewModel.setDeveloperMode(!ui.developerMode) },
                leading = {
                    androidx.compose.material3.Icon(
                        Icons.Filled.Code,
                        contentDescription = null,
                        tint = MaterialTheme.colorScheme.onSurfaceVariant,
                        modifier = Modifier.size(22.dp),
                    )
                },
                trailing = {
                    Switch(
                        checked = ui.developerMode,
                        onCheckedChange = viewModel::setDeveloperMode,
                    )
                },
                showDivider = true,
            )
            SettingsRow(
                title = "检查更新",
                onClick = onUpdate,
                leading = {
                    androidx.compose.material3.Icon(
                        Icons.Filled.SystemUpdate,
                        contentDescription = null,
                        tint = MaterialTheme.colorScheme.onSurfaceVariant,
                        modifier = Modifier.size(22.dp),
                    )
                },
                trailing = {
                    Row(verticalAlignment = androidx.compose.ui.Alignment.CenterVertically) {
                        if (ui.updateBadge) {
                            Badge(modifier = Modifier.padding(end = 6.dp))
                        }
                        if (ui.checkingUpdate) {
                            CircularProgressIndicator(
                                modifier = Modifier.size(16.dp),
                                strokeWidth = 1.5.dp,
                            )
                        } else {
                            Text(
                                text = ui.appVersion,
                                style = MaterialTheme.typography.bodyMedium,
                                color = MaterialTheme.colorScheme.onSurfaceVariant,
                            )
                        }
                    }
                },
                showDivider = true,
            )
            SettingsRow(
                title = stringResource(R.string.mine_version),
                onClick = onVersionInfo,
                leading = {
                    androidx.compose.material3.Icon(
                        Icons.Filled.Settings,
                        contentDescription = null,
                        tint = MaterialTheme.colorScheme.onSurfaceVariant,
                        modifier = Modifier.size(22.dp),
                    )
                },
                showDivider = true,
            )
            SettingsRow(
                title = stringResource(R.string.mine_about),
                onClick = onAbout,
                leading = {
                    androidx.compose.material3.Icon(
                        Icons.Filled.Info,
                        contentDescription = null,
                        tint = MaterialTheme.colorScheme.onSurfaceVariant,
                        modifier = Modifier.size(22.dp),
                    )
                },
                showDivider = ui.developerMode || crashLog != null,
            )
            // 诊断日志是流式排障工具，普通用户无感知需求，收进开发者模式
            if (ui.developerMode) {
                SettingsRow(
                    title = "诊断日志",
                    onClick = {
                        probeLogVersion++
                        showProbeLog = true
                    },
                    leading = {
                        androidx.compose.material3.Icon(
                            Icons.Filled.Timeline,
                            contentDescription = null,
                            tint = MaterialTheme.colorScheme.onSurfaceVariant,
                            modifier = Modifier.size(22.dp),
                        )
                    },
                    showDivider = crashLog != null,
                )
            }
            if (crashLog != null) {
                SettingsRow(
                    title = "崩溃日志",
                    onClick = { showCrashLog = true },
                    leading = {
                        androidx.compose.material3.Icon(
                            Icons.Filled.BugReport,
                            contentDescription = null,
                            tint = MaterialTheme.colorScheme.error,
                            modifier = Modifier.size(22.dp),
                        )
                    },
                    titleColor = MaterialTheme.colorScheme.error,
                    showDivider = false,
                )
            }
        }

        Spacer(Modifier.weight(1f))

        // ── 切换账号 + 退出登录 ──
        SettingsCard {
            SettingsRow(
                title = "切换账号",
                onClick = onSwitchAccount,
                leading = {
                    androidx.compose.material3.Icon(
                        Icons.Filled.ManageAccounts,
                        contentDescription = null,
                        tint = MaterialTheme.colorScheme.onSurfaceVariant,
                        modifier = Modifier.size(22.dp),
                    )
                },
                showDivider = true,
            )
            SettingsRow(
                title = stringResource(R.string.mine_logout),
                onClick = { viewModel.logout(onLogout) },
                titleColor = Color(0xFFE53935),
                leading = {
                    androidx.compose.material3.Icon(
                        Icons.AutoMirrored.Filled.Logout,
                        contentDescription = null,
                        tint = Color(0xFFE53935),
                        modifier = Modifier.size(22.dp),
                    )
                },
                showDivider = false,
            )
        }
    }

    if (showCrashLog && crashLog != null) {
        AlertDialog(
            onDismissRequest = { showCrashLog = false },
            title = { Text("崩溃日志") },
            text = {
                Text(
                    text = crashLog,
                    style = MaterialTheme.typography.bodySmall,
                    modifier = Modifier.verticalScroll(rememberScrollState()),
                )
            },
            confirmButton = {
                TextButton(onClick = { showCrashLog = false }) { Text("关闭") }
            },
        )
    }

    if (showProbeLog) {
        AlertDialog(
            onDismissRequest = { showProbeLog = false },
            title = { Text("诊断日志") },
            text = {
                SelectionContainer {
                    Text(
                        text = probeLog.ifBlank { "暂无日志，先去聊天页发一条消息再回来看" },
                        style = MaterialTheme.typography.bodySmall,
                        modifier = Modifier.verticalScroll(rememberScrollState()),
                    )
                }
            },
            confirmButton = {
                TextButton(onClick = {
                    clipboard.setText(AnnotatedString(probeLog))
                    android.widget.Toast.makeText(context, "已复制", android.widget.Toast.LENGTH_SHORT).show()
                }) { Text("复制") }
            },
            dismissButton = {
                TextButton(onClick = {
                    StreamProbe.clearLog()
                    probeLogVersion++
                }) { Text("清空") }
            },
        )
    }
}

private fun readCrashLog(context: android.content.Context): String? {
    return runCatching {
        File(context.filesDir, "crashes/last_crash.txt").takeIf { it.exists() }?.readText()
    }.getOrNull()
}
