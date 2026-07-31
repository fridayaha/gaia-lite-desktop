package com.unionagents.enduser.ui.workspace

import com.unionagents.enduser.net.dto.WorkspaceFileEntry

data class WorkspaceUiState(
    val agentId: String? = null,
    val path: String = ".",
    val stack: List<String> = listOf("."),
    val entries: List<WorkspaceFileEntry> = emptyList(),
    val loading: Boolean = false,
    val error: String? = null,
    val developerMode: Boolean = false,
    val searchQuery: String = "",
    val selectionMode: Boolean = false,
    val selectedPaths: Set<String> = emptySet(),
) {
    /** 经搜索过滤后的条目（保持目录在前排序）。 */
    val filteredEntries: List<WorkspaceFileEntry>
        get() = if (searchQuery.isBlank()) {
            entries
        } else {
            val q = searchQuery.lowercase()
            entries.filter { it.name.lowercase().contains(q) }
        }
}

/**
 * 工作区根目录下的 Hermes 内部目录黑名单——开发者模式关闭时隐藏。
 * 这些是引擎自身维护的运行时目录，对用户不可见，不应在「云盘」里露出。
 */
val WORKSPACE_HIDDEN_DIRS: Set<String> = setOf(
    "audio_cache",
    "cache",
    "cron",
    "home",
    "hooks",
    "image_cache",
    "logs",
    "memories",
    "pairing",
    "plans",
    "platforms",
    "plugins",
    "sandboxes",
    "sessions",
    "skills",
    "skins",
    "workspace",
)

