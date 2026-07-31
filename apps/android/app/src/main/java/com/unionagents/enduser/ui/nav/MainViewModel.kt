package com.unionagents.enduser.ui.nav

import androidx.lifecycle.ViewModel
import androidx.lifecycle.viewModelScope
import com.unionagents.enduser.repo.DeveloperModeStore
import com.unionagents.enduser.repo.UpdateBadgeStore
import dagger.hilt.android.lifecycle.HiltViewModel
import kotlinx.coroutines.flow.SharingStarted
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.stateIn
import javax.inject.Inject

@HiltViewModel
class MainViewModel @Inject constructor(
    developerModeStore: DeveloperModeStore,
    updateBadgeStore: UpdateBadgeStore,
) : ViewModel() {
    val developerMode: StateFlow<Boolean> = developerModeStore.flow
        .stateIn(viewModelScope, SharingStarted.WhileSubscribed(5000), false)

    /** 有新版未看：「我的」tab 打红点 */
    val updateBadge: StateFlow<Boolean> = updateBadgeStore.badgeVisible
        .stateIn(viewModelScope, SharingStarted.WhileSubscribed(5000), false)
}
