package com.unionagents.enduser.ui.workspace

import android.net.Uri
import androidx.lifecycle.SavedStateHandle
import androidx.lifecycle.ViewModel
import androidx.lifecycle.viewModelScope
import com.unionagents.enduser.repo.DeveloperModeStore
import com.unionagents.enduser.repo.WorkspaceRepository
import dagger.hilt.android.lifecycle.HiltViewModel
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.flow.update
import kotlinx.coroutines.launch
import retrofit2.HttpException
import javax.inject.Inject

@HiltViewModel
class WorkspaceViewModel @Inject constructor(
    savedStateHandle: SavedStateHandle,
    private val workspaceRepository: WorkspaceRepository,
    private val developerModeStore: DeveloperModeStore,
) : ViewModel() {

    private val _ui = MutableStateFlow(WorkspaceUiState())
    val ui: StateFlow<WorkspaceUiState> = _ui.asStateFlow()

    init {
        val agentId = savedStateHandle.get<String>("agentId")
        if (agentId != null) {
            _ui.update { it.copy(agentId = agentId) }
            loadDir(".")
        }
        viewModelScope.launch {
            developerModeStore.flow.collect { enabled ->
                // 切换开发者模式后即时重拉当前目录，根目录下隐藏/显示目录立即生效
                _ui.update { it.copy(developerMode = enabled) }
                val current = _ui.value
                if (current.path == ".") {
                    loadDir(current.path)
                }
            }
        }
    }

    fun setAgentId(agentId: String) {
        if (_ui.value.agentId == agentId) return
        _ui.update {
            it.copy(
                agentId = agentId,
                path = ".",
                stack = listOf("."),
                selectedPaths = emptySet(),
                selectionMode = false,
                searchQuery = "",
            )
        }
        loadDir(".")
    }

    /**
     * 应用开发者模式过滤：根目录下且关闭开发者模式时隐藏 Hermes 内部目录。
     * 非根目录一律展示（用户一旦进到黑名单目录里，里面的文件正常显示）。
     */
    private fun List<com.unionagents.enduser.net.dto.WorkspaceFileEntry>.applyFilter(): List<com.unionagents.enduser.net.dto.WorkspaceFileEntry> =
        if (_ui.value.developerMode || _ui.value.path != ".") this
        else filterNot { it.isDir && it.name in WORKSPACE_HIDDEN_DIRS }

    fun loadDir(path: String) {
        val agentId = _ui.value.agentId ?: return
        viewModelScope.launch {
            _ui.update { it.copy(loading = true, error = null, path = path) }
            try {
                val list = workspaceRepository.listFiles(agentId, path)
                _ui.update {
                    it.copy(
                        loading = false,
                        entries = list.entries.applyFilter(),
                        stack = if (path == ".") listOf(".") else it.stack + path,
                        selectedPaths = emptySet(),
                        selectionMode = false,
                    )
                }
            } catch (e: HttpException) {
                val friendly = when (e.code()) {
                    403 -> "尚未初始化工作区，请先返回会话发送一条消息后再查看"
                    404 -> "路径不存在"
                    else -> e.message() ?: "加载失败"
                }
                _ui.update { it.copy(loading = false, error = friendly) }
            } catch (e: Throwable) {
                _ui.update { it.copy(loading = false, error = e.message ?: "加载失败") }
            }
        }
    }

    fun openEntry(entry: com.unionagents.enduser.net.dto.WorkspaceFileEntry): String? {
        if (!entry.isDir) return null
        val newPath = if (_ui.value.path == ".") entry.name else "${_ui.value.path}/${entry.name}"
        loadDir(newPath)
        return null
    }

    fun goBack(): Boolean {
        val stack = _ui.value.stack
        if (stack.size <= 1) return false
        val newStack = stack.dropLast(1)
        val newPath = newStack.last()
        viewModelScope.launch {
            val agentId = _ui.value.agentId ?: return@launch
            _ui.update { it.copy(loading = true, error = null, path = newPath, stack = newStack) }
            try {
                val list = workspaceRepository.listFiles(agentId, newPath)
                _ui.update {
                    it.copy(
                        loading = false,
                        entries = list.entries.applyFilter(),
                        selectedPaths = emptySet(),
                        selectionMode = false,
                    )
                }
            } catch (e: Throwable) {
                _ui.update { it.copy(loading = false, error = e.message ?: "加载失败") }
            }
        }
        return true
    }

    fun refresh() {
        loadDir(_ui.value.path)
    }

    // ── 搜索 ──

    fun setSearchQuery(query: String) {
        _ui.update { it.copy(searchQuery = query) }
    }

    // ── 多选 ──

    fun enterSelectionMode(path: String) {
        _ui.update { it.copy(selectionMode = true, selectedPaths = setOf(path)) }
    }

    fun exitSelectionMode() {
        _ui.update { it.copy(selectionMode = false, selectedPaths = emptySet()) }
    }

    fun toggleSelection(path: String) {
        _ui.update { state ->
            val selected = state.selectedPaths.toMutableSet()
            if (path in selected) selected.remove(path) else selected.add(path)
            state.copy(selectedPaths = selected)
        }
    }

    fun selectAll() {
        _ui.update { it.copy(selectedPaths = it.filteredEntries.map { e -> e.path }.toSet()) }
    }

    // ── 文件操作 ──

    fun createFolder(name: String, onResult: (Boolean, String?) -> Unit = { _, _ -> }) {
        val agentId = _ui.value.agentId ?: return
        val parent = _ui.value.path
        viewModelScope.launch {
            try {
                workspaceRepository.createFolder(agentId, parent, name)
                refresh()
                onResult(true, null)
            } catch (e: HttpException) {
                val msg = when (e.code()) {
                    409 -> "文件名或文件夹名已存在"
                    400 -> "该名称被系统保留，不可使用"
                    else -> e.message() ?: "创建失败"
                }
                onResult(false, msg)
            } catch (e: Throwable) {
                onResult(false, e.message ?: "创建失败")
            }
        }
    }

    fun uploadFile(uri: Uri, onResult: (Boolean, String?) -> Unit = { _, _ -> }) {
        val agentId = _ui.value.agentId ?: return
        val dir = _ui.value.path
        viewModelScope.launch {
            try {
                workspaceRepository.uploadFile(agentId, dir, uri)
                refresh()
                onResult(true, null)
            } catch (e: Throwable) {
                onResult(false, e.message ?: "上传失败")
            }
        }
    }

    fun deleteFile(path: String, onResult: (Boolean, String?) -> Unit = { _, _ -> }) {
        val agentId = _ui.value.agentId ?: return
        viewModelScope.launch {
            try {
                workspaceRepository.deleteFile(agentId, path)
                _ui.update { state ->
                    state.copy(
                        entries = state.entries.filter { it.path != path },
                        selectedPaths = state.selectedPaths - path,
                    )
                }
                onResult(true, null)
            } catch (e: Throwable) {
                onResult(false, e.message ?: "删除失败")
            }
        }
    }

    fun moveFile(fromPath: String, toPath: String, onResult: (Boolean, String?) -> Unit = { _, _ -> }) {
        val agentId = _ui.value.agentId ?: return
        viewModelScope.launch {
            try {
                workspaceRepository.moveFile(agentId, fromPath, toPath)
                refresh()
                onResult(true, null)
            } catch (e: HttpException) {
                val msg = when (e.code()) {
                    409 -> "目标位置已存在同名文件"
                    404 -> "源文件不存在"
                    else -> e.message() ?: "移动失败"
                }
                onResult(false, msg)
            } catch (e: Throwable) {
                onResult(false, e.message ?: "移动失败")
            }
        }
    }

    suspend fun downloadFile(path: String): ByteArray? {
        val agentId = _ui.value.agentId ?: return null
        return try {
            workspaceRepository.downloadFile(agentId, path)
        } catch (e: Throwable) {
            null
        }
    }
}

