package com.unionagents.enduser.ui.nav

import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.automirrored.filled.Chat
import androidx.compose.material.icons.filled.Folder
import androidx.compose.material.icons.filled.Person
import androidx.compose.material3.Badge
import androidx.compose.material3.BadgedBox
import androidx.compose.material3.HorizontalDivider
import androidx.compose.material3.Icon
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.NavigationBar
import androidx.compose.material3.NavigationBarItem
import androidx.compose.material3.NavigationBarItemDefaults
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.vector.ImageVector
import androidx.compose.ui.res.stringResource
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import com.unionagents.enduser.R

@Composable
fun BottomBar(
    currentRoute: String?,
    developerMode: Boolean,
    onTabClick: (String) -> Unit,
    mineBadge: Boolean = false,
) {
    val chatLabel = stringResource(R.string.tab_chat)
    val mineLabel = stringResource(R.string.tab_mine)
    val workspaceLabel = stringResource(
        if (developerMode) R.string.tab_workspace else R.string.tab_cloud_drive,
    )
    val tabs = listOf(
        BottomTab(chatLabel, Routes.AGENT_LIST, Icons.AutoMirrored.Filled.Chat),
        BottomTab(workspaceLabel, Routes.WORKSPACE_TAB, Icons.Filled.Folder),
        BottomTab(mineLabel, Routes.MINE, Icons.Filled.Person),
    )
    Column {
        HorizontalDivider(color = MaterialTheme.colorScheme.outlineVariant)
        NavigationBar(
            containerColor = MaterialTheme.colorScheme.surface,
            tonalElevation = 0.dp,
        ) {
            tabs.forEach { tab ->
                val selected = currentRoute == tab.route
                val primary = MaterialTheme.colorScheme.primary
                val onSurfaceVariant = MaterialTheme.colorScheme.onSurfaceVariant
                val surface = MaterialTheme.colorScheme.surface
                NavigationBarItem(
                    selected = selected,
                    onClick = { onTabClick(tab.route) },
                    icon = {
                        val tabIcon: @Composable () -> Unit = {
                            Icon(
                                tab.icon,
                                contentDescription = null,
                                modifier = Modifier.size(24.dp),
                            )
                        }
                        if (tab.route == Routes.MINE && mineBadge) {
                            BadgedBox(badge = { Badge() }) { tabIcon() }
                        } else {
                            tabIcon()
                        }
                    },
                    label = {
                        Text(
                            tab.label,
                            fontWeight = if (selected) FontWeight.SemiBold else FontWeight.Normal,
                        )
                    },
                    colors = NavigationBarItemDefaults.colors(
                        selectedIconColor = primary,
                        selectedTextColor = primary,
                        unselectedIconColor = onSurfaceVariant,
                        unselectedTextColor = onSurfaceVariant,
                        indicatorColor = surface,
                    ),
                )
            }
        }
    }
}

private data class BottomTab(
    val label: String,
    val route: String,
    val icon: ImageVector,
)
