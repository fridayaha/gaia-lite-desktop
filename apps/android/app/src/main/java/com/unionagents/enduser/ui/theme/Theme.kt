package com.unionagents.enduser.ui.theme

import androidx.compose.foundation.isSystemInDarkTheme
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.darkColorScheme
import androidx.compose.material3.lightColorScheme
import androidx.compose.runtime.Composable
import androidx.compose.ui.graphics.Color

// 全部槽位显式指定——M3 未指定的 token 落回默认紫色系（primaryContainer #EADDFF、
// secondaryContainer #E8DEF8、surfaceContainer* 微紫），会在选中态/卡片/输入框/菜单
// 背景透出淡紫色，与本主题的中性灰蓝不一致。
private val DarkScheme = darkColorScheme(
    primary = DarkAccent,
    onPrimary = DarkText,
    primaryContainer = DarkPrimaryContainer,
    onPrimaryContainer = DarkOnPrimaryContainer,
    inversePrimary = LightAccent,
    secondary = DarkMuted,
    onSecondary = DarkBg,
    secondaryContainer = DarkSurfaceSubtle,
    onSecondaryContainer = DarkText,
    tertiary = DarkMuted,
    onTertiary = DarkBg,
    tertiaryContainer = DarkSurfaceSubtle,
    onTertiaryContainer = DarkText,
    background = DarkBg,
    onBackground = DarkText,
    surface = DarkSurface,
    onSurface = DarkText,
    surfaceVariant = DarkSurfaceSubtle,
    onSurfaceVariant = DarkMuted,
    surfaceTint = DarkAccent,
    surfaceBright = DarkSurfaceSubtle,
    surfaceDim = DarkSurfaceDim,
    surfaceContainer = DarkSurface,
    surfaceContainerHigh = DarkSurfaceSubtle,
    surfaceContainerHighest = DarkSurfaceContainerHighest,
    surfaceContainerLow = DarkBg,
    surfaceContainerLowest = DarkSurfaceContainerLowest,
    inverseSurface = DarkText,
    inverseOnSurface = DarkBg,
    outline = DarkBorder,
    outlineVariant = DarkBorder,
    error = DarkError,
    onError = DarkText,
    errorContainer = DarkErrorContainer,
    onErrorContainer = DarkOnErrorContainer,
    scrim = Color.Black,
)

private val LightScheme = lightColorScheme(
    primary = LightAccent,
    onPrimary = LightSurface,
    primaryContainer = LightPrimaryContainer,
    onPrimaryContainer = LightOnPrimaryContainer,
    inversePrimary = LightInversePrimary,
    secondary = LightMuted,
    onSecondary = LightSurface,
    secondaryContainer = LightSurfaceSubtle,
    onSecondaryContainer = LightText,
    tertiary = LightMuted,
    onTertiary = LightSurface,
    tertiaryContainer = LightSurfaceSubtle,
    onTertiaryContainer = LightText,
    background = LightBg,
    onBackground = LightText,
    surface = LightSurface,
    onSurface = LightText,
    surfaceVariant = LightSurfaceSubtle,
    onSurfaceVariant = LightMuted,
    surfaceTint = LightAccent,
    surfaceBright = LightSurface,
    surfaceDim = LightSurfaceSubtle,
    surfaceContainer = LightSurfaceSubtle,
    surfaceContainerHigh = LightSurfaceContainerHigh,
    surfaceContainerHighest = LightBorder,
    surfaceContainerLow = LightBg,
    surfaceContainerLowest = LightSurface,
    inverseSurface = LightInverseSurface,
    inverseOnSurface = LightBg,
    outline = LightBorder,
    outlineVariant = LightBorder,
    error = LightError,
    onError = LightSurface,
    errorContainer = LightErrorContainer,
    onErrorContainer = LightOnErrorContainer,
    scrim = Color.Black,
)

@Composable
fun UnionAgentsTheme(
    darkTheme: Boolean = isSystemInDarkTheme(),
    content: @Composable () -> Unit,
) {
    MaterialTheme(
        colorScheme = if (darkTheme) DarkScheme else LightScheme,
        typography = AppTypography,
        content = content,
    )
}
