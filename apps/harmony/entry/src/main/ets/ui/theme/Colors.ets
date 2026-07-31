/**
 * 颜色定义。复刻 Android ui/theme/Color.kt。
 * 深色参考 enduser 门户 style.css。
 */

export class AppColors {
  // 深色
  static readonly darkBg = 0xFF1A1A2E
  static readonly darkSurface = 0xFF232342
  static readonly darkSurfaceSubtle = 0xFF2A2A4A
  static readonly darkText = 0xFFF3F4F6
  static readonly darkMuted = 0xFF9CA3AF
  static readonly darkBorder = 0xFF2D2D4F
  static readonly darkAccent = 0xFF3B82F6
  static readonly darkError = 0xFFE94560

  // 浅色
  static readonly lightBg = 0xFFF9FAFB
  static readonly lightSurface = 0xFFFFFFFF
  static readonly lightSurfaceSubtle = 0xFFF3F4F6
  static readonly lightText = 0xFF1F2937
  static readonly lightMuted = 0xFF6B7280
  static readonly lightBorder = 0xFFE5E7EB
  // android M3 outlineVariant（divider 专用，主题只覆盖 primary，该槽位保留 M3 基线值）
  static readonly lightBorderVariant = 0xFFCAC4D0
  // android M3 primaryContainer/onPrimaryContainer（主题只覆盖 primary，保留 M3 基线值）
  static readonly lightAccentContainer = 0xFFEADDFF
  static readonly lightOnAccentContainer = 0xFF21005D
  static readonly lightAccent = 0xFF2563EB
  static readonly lightError = 0xFFDC2626
}

export interface ThemeColors {
  bg: number
  surface: number
  surfaceSubtle: number
  surfaceVariant: number
  text: number
  muted: number
  border: number
  borderVariant: number
  accent: number
  accentContainer: number
  onAccentContainer: number
  error: number
  onAccent: number
}

export const DarkTheme: ThemeColors = {
  bg: AppColors.darkBg,
  surface: AppColors.darkSurface,
  surfaceSubtle: AppColors.darkSurfaceSubtle,
  surfaceVariant: AppColors.darkSurfaceSubtle,
  text: AppColors.darkText,
  muted: AppColors.darkMuted,
  border: AppColors.darkBorder,
  borderVariant: AppColors.darkBorder,
  accent: AppColors.darkAccent,
  accentContainer: AppColors.darkSurfaceSubtle,
  onAccentContainer: AppColors.darkText,
  error: AppColors.darkError,
  onAccent: AppColors.darkText,
}

export const LightTheme: ThemeColors = {
  bg: AppColors.lightBg,
  surface: AppColors.lightSurface,
  surfaceSubtle: AppColors.lightSurfaceSubtle,
  surfaceVariant: AppColors.lightSurfaceSubtle,
  text: AppColors.lightText,
  muted: AppColors.lightMuted,
  border: AppColors.lightBorder,
  borderVariant: AppColors.lightBorderVariant,
  accent: AppColors.lightAccent,
  accentContainer: AppColors.lightAccentContainer,
  onAccentContainer: AppColors.lightOnAccentContainer,
  error: AppColors.lightError,
  onAccent: AppColors.lightSurface,
}

export function themeFor(isDark: boolean): ThemeColors {
  return isDark ? DarkTheme : LightTheme
}
