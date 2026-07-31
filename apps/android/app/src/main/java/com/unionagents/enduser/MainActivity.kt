package com.unionagents.enduser

import android.os.Bundle
import androidx.activity.ComponentActivity
import androidx.activity.compose.setContent
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Surface
import androidx.compose.ui.Modifier
import com.unionagents.enduser.ui.nav.RootNavGraph
import com.unionagents.enduser.ui.theme.UnionAgentsTheme
import dagger.hilt.android.AndroidEntryPoint

@AndroidEntryPoint
class MainActivity : ComponentActivity() {
    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        // 不开 edge-to-edge：让 DecorView fit system windows，这样 manifest 的
        // adjustResize 才会真正 resize window（Android 11+ 上 setDecorFitsSystemWindows(false)
        // 会让 adjustResize 失效，只发 WindowInsets，某些设备 inset 传递不可靠 → Composer 被键盘压住）。
        // 代价：status bar 显示 themes.xml 里的 brand_primary 紫色背景，不是透明。
        setContent {
            UnionAgentsTheme {
                Surface(
                    modifier = Modifier.fillMaxSize(),
                    color = MaterialTheme.colorScheme.background,
                ) {
                    RootNavGraph()
                }
            }
        }
    }
}
