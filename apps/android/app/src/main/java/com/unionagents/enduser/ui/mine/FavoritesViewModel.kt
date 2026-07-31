package com.unionagents.enduser.ui.mine

import androidx.lifecycle.ViewModel
import androidx.lifecycle.viewModelScope
import com.unionagents.enduser.net.dto.FavoriteItem
import com.unionagents.enduser.repo.MessageFeedbackRepository
import dagger.hilt.android.lifecycle.HiltViewModel
import javax.inject.Inject
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.flow.update
import kotlinx.coroutines.launch

data class FavoritesUiState(
    val loading: Boolean = true,
    val items: List<FavoriteItem> = emptyList(),
    val error: String? = null,
)

@HiltViewModel
class FavoritesViewModel @Inject constructor(
    private val messageFeedbackRepository: MessageFeedbackRepository,
) : ViewModel() {

    private val _ui = MutableStateFlow(FavoritesUiState())
    val ui: StateFlow<FavoritesUiState> = _ui.asStateFlow()

    init {
        load()
    }

    fun load() {
        viewModelScope.launch {
            _ui.update { it.copy(loading = true, error = null) }
            try {
                val items = messageFeedbackRepository.listMyFavorites()
                _ui.update { it.copy(loading = false, items = items) }
            } catch (e: Throwable) {
                _ui.update { it.copy(loading = false, error = e.message ?: "加载失败") }
            }
        }
    }
}
