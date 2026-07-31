package com.unionagents.enduser.ui.nav

import android.net.Uri
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.WindowInsets
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.padding
import androidx.compose.material3.CircularProgressIndicator
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Scaffold
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.LocalContext
import androidx.hilt.navigation.compose.hiltViewModel
import androidx.lifecycle.compose.collectAsStateWithLifecycle
import androidx.navigation.NavGraph.Companion.findStartDestination
import androidx.navigation.NavType
import androidx.navigation.compose.NavHost
import androidx.navigation.compose.composable
import androidx.navigation.compose.currentBackStackEntryAsState
import androidx.navigation.compose.rememberNavController
import androidx.navigation.navArgument
import com.unionagents.enduser.net.SessionController
import com.unionagents.enduser.repo.AuthRepository
import com.unionagents.enduser.repo.LastAgentStore
import com.unionagents.enduser.ui.agentlist.AgentListScreen
import com.unionagents.enduser.ui.login.LoginScreen
import com.unionagents.enduser.ui.mine.MineScreen
import com.unionagents.enduser.ui.mine.SettingsScreen
import com.unionagents.enduser.ui.mine.SwitchAccountScreen
import com.unionagents.enduser.ui.mine.UpdateScreen
import com.unionagents.enduser.ui.workspace.WorkspaceScreen
import com.unionagents.enduser.ui.workspace.WorkspaceTabScreen
import dagger.hilt.EntryPoint
import dagger.hilt.android.EntryPointAccessors
import dagger.hilt.InstallIn
import dagger.hilt.components.SingletonComponent
import kotlinx.coroutines.flow.first
import kotlinx.coroutines.launch

@Composable
fun RootNavGraph() {
    val navController = rememberNavController()
    val mainViewModel: MainViewModel = hiltViewModel()
    val developerMode by mainViewModel.developerMode.collectAsStateWithLifecycle()
    val updateBadge by mainViewModel.updateBadge.collectAsStateWithLifecycle()
    val context = LocalContext.current
    val authRepository = remember {
        EntryPointAccessors.fromApplication(context, AuthEntryPoint::class.java).authRepository()
    }
    val lastAgentStore = remember {
        EntryPointAccessors.fromApplication(context, LastAgentStoreEntryPoint::class.java).lastAgentStore()
    }
    val sessionController = remember {
        EntryPointAccessors.fromApplication(context, SessionControllerEntryPoint::class.java).sessionController()
    }

    // 冷启动/登录后若记住过最后使用的智能体，直接进入该智能体会话；否则进对话首页。
    var resolvedStart by remember { mutableStateOf<String?>(null) }
    LaunchedEffect(Unit) {
        val currentToken = runCatching { authRepository.tokenFlow.first() }.getOrNull()
        val saved = runCatching { lastAgentStore.get() }.getOrNull()
        resolvedStart = when {
            currentToken != null && saved?.agentId != null -> "chat/${saved.agentId}"
            currentToken != null -> Routes.AGENT_LIST
            else -> Routes.LOGIN
        }
    }
    val startDestination = resolvedStart

    val scope = rememberCoroutineScope()

    // refresh_token 失效时全局跳登录（Authenticator / TokenRefresher / ensureSession 三处触发）
    LaunchedEffect(Unit) {
        sessionController.forceLogout.collect {
            authRepository.logout()
            navController.navigate(Routes.LOGIN) {
                popUpTo(0) { inclusive = true }
            }
        }
    }

    val backStackEntry by navController.currentBackStackEntryAsState()
    val currentRoute = backStackEntry?.destination?.route
    val showBottomBar = currentRoute == Routes.AGENT_LIST ||
        currentRoute == Routes.MINE ||
        currentRoute == Routes.WORKSPACE_TAB

    Scaffold(
        bottomBar = {
            if (showBottomBar) {
                BottomBar(
                    currentRoute = currentRoute,
                    developerMode = developerMode,
                    mineBadge = updateBadge,
                    onTabClick = { route ->
                        navController.navigate(route) {
                            popUpTo(navController.graph.findStartDestination().id) { saveState = true }
                            launchSingleTop = true
                            restoreState = true
                        }
                    },
                )
            }
        },
        // 非 edge-to-edge（见 MainActivity）：DecorView 已 fit system windows，
        // content view 内 WindowInsets.* 全为 0，Scaffold 不需要再分发 inset。
        contentWindowInsets = WindowInsets(0, 0, 0, 0),
    ) { padding ->
        if (startDestination == null) {
            Box(
                modifier = Modifier.fillMaxSize(),
                contentAlignment = Alignment.Center,
            ) {
                CircularProgressIndicator()
            }
            return@Scaffold
        }
        NavHost(
            navController = navController,
            startDestination = startDestination,
            modifier = Modifier.fillMaxSize(),
        ) {
            composable(Routes.LOGIN) {
                LoginScreen(
                    onLoggedIn = {
                        scope.launch {
                            val saved = runCatching { lastAgentStore.get() }.getOrNull()
                            if (saved?.agentId != null) {
                                navController.navigate("chat/${saved.agentId}") {
                                    popUpTo(Routes.LOGIN) { inclusive = true }
                                }
                            } else {
                                navController.navigate(Routes.AGENT_LIST) {
                                    popUpTo(Routes.LOGIN) { inclusive = true }
                                }
                            }
                        }
                    },
                )
            }
            composable(Routes.AGENT_LIST) {
                AgentListScreen(
                    onAgentClick = { agentId ->
                        navController.navigate("chat/$agentId")
                    },
                )
            }
            composable(Routes.MINE) {
                Box(modifier = Modifier.fillMaxSize().padding(padding)) {
                    MineScreen(
                        onOpenSettings = { navController.navigate(Routes.SETTINGS) },
                        onEditProfile = { navController.navigate(Routes.PROFILE_EDIT) },
                        onOpenFavorites = { navController.navigate(Routes.MINE_FAVORITES) },
                        onOpenSession = { agentId, sessionId ->
                            navController.navigate("chat/$agentId?sessionId=$sessionId")
                        },
                        onLogout = {
                            navController.navigate(Routes.LOGIN) {
                                popUpTo(Routes.AGENT_LIST) { inclusive = true }
                            }
                        },
                    )
                }
            }
            composable(Routes.MINE_FAVORITES) {
                com.unionagents.enduser.ui.mine.FavoritesScreen(
                    onBack = { navController.popBackStack() },
                    onOpenSession = { agentId, sessionId ->
                        navController.navigate("chat/$agentId?sessionId=$sessionId")
                    },
                )
            }
            composable(Routes.SETTINGS) {
                SettingsScreen(
                    onBack = { navController.popBackStack() },
                    onOpenAccountSettings = { navController.navigate(Routes.ACCOUNT_SETTINGS) },
                    onSwitchAccount = { navController.navigate(Routes.SWITCH_ACCOUNT) },
                    onAbout = { navController.navigate(Routes.ABOUT) },
                    onVersionInfo = { navController.navigate(Routes.VERSION_INFO) },
                    onUpdate = { navController.navigate(Routes.UPDATE) },
                    onLogout = {
                        navController.navigate(Routes.LOGIN) {
                            popUpTo(Routes.AGENT_LIST) { inclusive = true }
                        }
                    },
                )
            }
            composable(Routes.PROFILE_EDIT) {
                com.unionagents.enduser.ui.mine.ProfileEditScreen(
                    onBack = { navController.popBackStack() },
                    onEditAvatar = { navController.navigate(Routes.EDIT_AVATAR) },
                    onEditField = { field -> navController.navigate("edit_text/$field") },
                )
            }
            composable(Routes.ACCOUNT_SETTINGS) {
                com.unionagents.enduser.ui.mine.AccountSettingsScreen(
                    onBack = { navController.popBackStack() },
                    onEditAvatar = { navController.navigate(Routes.EDIT_AVATAR) },
                    onEditField = { field -> navController.navigate("edit_text/$field") },
                    onVerifyContact = { channel -> navController.navigate("verify_contact/$channel") },
                )
            }
            composable(Routes.ABOUT) {
                com.unionagents.enduser.ui.mine.AboutScreen(
                    onBack = { navController.popBackStack() },
                )
            }
            composable(Routes.VERSION_INFO) {
                com.unionagents.enduser.ui.mine.VersionInfoScreen(
                    onBack = { navController.popBackStack() },
                )
            }
            composable(Routes.UPDATE) {
                UpdateScreen(
                    onBack = { navController.popBackStack() },
                )
            }
            composable(Routes.EDIT_AVATAR) {
                com.unionagents.enduser.ui.mine.EditAvatarScreen(
                    onBack = { navController.popBackStack() },
                )
            }
            composable(
                route = "edit_text/{field}",
                arguments = listOf(
                    navArgument("field") { type = NavType.StringType },
                ),
            ) { backStackEntry ->
                val field = backStackEntry.arguments?.getString("field") ?: return@composable
                com.unionagents.enduser.ui.mine.EditTextFieldScreen(
                    field = field,
                    onBack = { navController.popBackStack() },
                )
            }
            composable(
                route = "verify_contact/{channel}",
                arguments = listOf(
                    navArgument("channel") { type = NavType.StringType },
                ),
            ) { backStackEntry ->
                val channel = backStackEntry.arguments?.getString("channel") ?: return@composable
                com.unionagents.enduser.ui.mine.VerifyContactScreen(
                    channel = channel,
                    onBack = { navController.popBackStack() },
                )
            }
            composable(Routes.SWITCH_ACCOUNT) {
                SwitchAccountScreen(
                    onBack = { navController.popBackStack() },
                    onAddAccount = {
                        navController.navigate(Routes.LOGIN) {
                            popUpTo(Routes.AGENT_LIST) { inclusive = true }
                        }
                    },
                    onSwitchAccount = {
                        navController.navigate(Routes.AGENT_LIST) {
                            popUpTo(Routes.AGENT_LIST) { inclusive = true }
                        }
                    },
                )
            }
            composable(
                route = "chat/{agentId}?sessionId={sessionId}",
                arguments = listOf(
                    navArgument("sessionId") {
                        type = NavType.StringType
                        nullable = true
                        defaultValue = null
                    },
                ),
            ) { backStackEntry ->
                val agentId = backStackEntry.arguments?.getString("agentId") ?: return@composable
                val onChatBack: () -> Unit = {
                    if (navController.previousBackStackEntry != null) {
                        navController.popBackStack()
                    } else {
                        // 从冷启动直达最近会话时，返回应回到智能体选择页
                        navController.navigate(Routes.AGENT_LIST) {
                            popUpTo(0) { inclusive = true }
                        }
                    }
                }
                // 系统返回手势不走 ChatScreen 的 onBack 回调：栈里只有 chat 时（冷启动直达）
                // 默认行为是退出 App，这里拦下走同一兜底；有上一页时行为与默认 pop 一致。
                androidx.activity.compose.BackHandler { onChatBack() }
                com.unionagents.enduser.ui.chat.ChatScreen(
                    onBack = onChatBack,
                    onOpenWorkspace = {
                        navController.navigate("workspace/$agentId")
                    },
                )
            }
            composable("workspace/{agentId}") { backStackEntry ->
                val agentId = backStackEntry.arguments?.getString("agentId") ?: return@composable
                com.unionagents.enduser.ui.workspace.WorkspaceScreen(
                    agentId = agentId,
                    onOpenFile = { path ->
                        navController.navigate("file/$agentId/${Uri.encode(path)}")
                    },
                    onBack = { navController.popBackStack() },
                    modifier = Modifier.padding(padding),
                )
            }
            composable(Routes.WORKSPACE_TAB) {
                WorkspaceTabScreen(
                    onOpenFile = { agentId, path ->
                        navController.navigate("file/$agentId/${Uri.encode(path)}")
                    },
                    modifier = Modifier.padding(padding),
                )
            }
            composable(
                route = "file/{agentId}/{path}",
                arguments = listOf(
                    navArgument("agentId") { type = NavType.StringType },
                    navArgument("path") { type = NavType.StringType },
                ),
            ) { backStackEntry ->
                val agentId = backStackEntry.arguments?.getString("agentId") ?: return@composable
                val path = backStackEntry.arguments?.getString("path")?.let { Uri.decode(it) } ?: return@composable
                val workspaceRepository = remember {
                    EntryPointAccessors.fromApplication(context, WorkspaceRepoEntryPoint::class.java).workspaceRepository()
                }
                com.unionagents.enduser.ui.workspace.FilePreviewScreen(
                    agentId = agentId,
                    path = path,
                    onBack = { navController.popBackStack() },
                    workspaceRepository = workspaceRepository,
                    modifier = Modifier.padding(padding),
                )
            }
        }
    }
}

@Composable
private fun WorkspacePlaceholder(agentId: String, modifier: Modifier = Modifier) {
    Box(
        modifier = modifier.fillMaxSize(),
        contentAlignment = Alignment.Center,
    ) {
        Text(
            text = "Workspace placeholder\nagentId=$agentId\n阶段 6 实现",
            style = MaterialTheme.typography.bodyMedium,
        )
    }
}

@EntryPoint
@InstallIn(SingletonComponent::class)
interface LastAgentStoreEntryPoint {
    fun lastAgentStore(): LastAgentStore
}

@EntryPoint
@InstallIn(SingletonComponent::class)
interface AuthEntryPoint {
    fun authRepository(): AuthRepository
}

@EntryPoint
@InstallIn(SingletonComponent::class)
interface SessionControllerEntryPoint {
    fun sessionController(): SessionController
}

@EntryPoint
@InstallIn(SingletonComponent::class)
interface WorkspaceRepoEntryPoint {
    fun workspaceRepository(): com.unionagents.enduser.repo.WorkspaceRepository
}
