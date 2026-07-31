package com.unionagents.enduser.ui.mine

import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.material3.Button
import androidx.compose.material3.CircularProgressIndicator
import androidx.compose.material3.LinearProgressIndicator
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import androidx.hilt.navigation.compose.hiltViewModel
import androidx.lifecycle.compose.collectAsStateWithLifecycle
import com.unionagents.enduser.net.dto.AppReleaseLatest

/**
 * 检查更新页：显示当前版本、最新版本信息，并支持下载更新。
 */
@Composable
fun UpdateScreen(
    onBack: () -> Unit,
    viewModel: MineViewModel = hiltViewModel(),
) {
    val ui by viewModel.ui.collectAsStateWithLifecycle()

    LaunchedEffect(Unit) {
        viewModel.checkForUpdate()
        // 看过即消红点（「我的」tab/设置齿轮/检查更新行），更新的版本发布再出现
        viewModel.markUpdateSeen()
    }

    SettingsScaffold(title = "检查更新", onBack = onBack) {
        Column(
            modifier = Modifier.fillMaxWidth(),
            verticalArrangement = Arrangement.spacedBy(12.dp),
        ) {
            SettingsCard {
                Column(
                    modifier = Modifier
                        .fillMaxWidth()
                        .padding(20.dp),
                    verticalArrangement = Arrangement.spacedBy(8.dp),
                ) {
                    Text(
                        text = "当前版本",
                        style = MaterialTheme.typography.labelMedium,
                        color = MaterialTheme.colorScheme.onSurfaceVariant,
                    )
                    Text(
                        text = "v${com.unionagents.enduser.BuildConfig.VERSION_NAME}",
                        style = MaterialTheme.typography.titleMedium,
                        fontWeight = FontWeight.SemiBold,
                    )
                }
            }

            SettingsCard {
                Column(
                    modifier = Modifier
                        .fillMaxWidth()
                        .padding(20.dp),
                    verticalArrangement = Arrangement.spacedBy(12.dp),
                    horizontalAlignment = Alignment.CenterHorizontally,
                ) {
                    when {
                        ui.checkingUpdate -> {
                            CircularProgressIndicator(modifier = Modifier.size(32.dp), strokeWidth = 2.dp)
                            Text(
                                text = "正在检查更新…",
                                style = MaterialTheme.typography.bodyMedium,
                                color = MaterialTheme.colorScheme.onSurfaceVariant,
                            )
                        }

                        ui.updateError != null -> {
                            Text(
                                text = ui.updateError ?: "检查更新失败",
                                style = MaterialTheme.typography.bodyMedium,
                                color = MaterialTheme.colorScheme.error,
                            )
                            TextButton(onClick = viewModel::checkForUpdate) {
                                Text("重试")
                            }
                        }

                        ui.updateAvailable && ui.latestRelease != null -> {
                            val release = ui.latestRelease!!
                            UpdateAvailableContent(
                                release = release,
                                downloadProgress = ui.downloadProgress,
                                onDownload = viewModel::startUpdateDownload,
                            )
                        }

                        ui.upToDate -> {
                            Text(
                                text = "已是最新版本",
                                style = MaterialTheme.typography.bodyLarge,
                                fontWeight = FontWeight.Medium,
                            )
                            ui.latestRelease?.let { latest ->
                                latest.version?.takeIf { it.isNotBlank() }?.let { v ->
                                    Text(
                                        text = "线上最新版本：v$v",
                                        style = MaterialTheme.typography.bodySmall,
                                        color = MaterialTheme.colorScheme.onSurfaceVariant,
                                    )
                                }
                            }
                        }

                        else -> {
                            Text(
                                text = ui.updateError ?: "检查更新失败",
                                style = MaterialTheme.typography.bodyMedium,
                                color = MaterialTheme.colorScheme.error,
                            )
                            TextButton(onClick = viewModel::checkForUpdate) {
                                Text("重试")
                            }
                        }
                    }
                }
            }
        }
    }
}

@Composable
private fun UpdateAvailableContent(
    release: AppReleaseLatest,
    downloadProgress: Float?,
    onDownload: () -> Unit,
) {
    val sizeText = release.size?.let { bytes ->
        val mb = bytes / 1024.0 / 1024.0
        if (mb >= 1) "约 ${"%.1f".format(mb)} MB" else "${bytes / 1024} KB"
    }

    Text(
        text = "发现新版本",
        style = MaterialTheme.typography.bodyLarge,
        fontWeight = FontWeight.Medium,
    )
    Text(
        text = release.version?.takeIf { it.isNotBlank() }?.let { "v$it" } ?: "最新版本",
        style = MaterialTheme.typography.titleMedium,
        fontWeight = FontWeight.SemiBold,
    )
    sizeText?.let {
        Text(
            text = it,
            style = MaterialTheme.typography.bodySmall,
            color = MaterialTheme.colorScheme.onSurfaceVariant,
        )
    }
    release.description?.ifBlank { null }?.let {
        Text(
            text = it,
            style = MaterialTheme.typography.bodySmall,
            color = MaterialTheme.colorScheme.onSurfaceVariant,
        )
    }

    if (downloadProgress != null) {
        Spacer(Modifier.size(4.dp))
        LinearProgressIndicator(
            progress = { downloadProgress },
            modifier = Modifier.fillMaxWidth(),
        )
        val pct = (downloadProgress * 100).toInt()
        Text(
            text = if (pct >= 100) "下载完成，等待安装…" else "下载中… $pct%",
            style = MaterialTheme.typography.bodySmall,
            color = MaterialTheme.colorScheme.onSurfaceVariant,
        )
    } else {
        Button(onClick = onDownload) {
            Text("下载更新")
        }
    }
}
