/**
 * 从 <html> 读取 CSS 主题变量。
 *
 * Cytoscape 等命令式 API 不接受 var()，需在运行时读取解析后的颜色值。
 * 调用方应在组件挂载后（getComputedStyle 可用时）调用。
 */
export function getThemeColor(name: string): string {
  if (typeof window === 'undefined') return '';
  const value = getComputedStyle(document.documentElement).getPropertyValue(name).trim();
  return value;
}

/** 主题色集合，供 Cytoscape 等命令式场景一次性读取。 */
export function getThemeColors() {
  return {
    surface: getThemeColor('--color-surface'),
    accent: getThemeColor('--color-accent'),
    accentHover: getThemeColor('--color-accent-hover'),
    text: getThemeColor('--color-text'),
    textSecondary: getThemeColor('--color-text-secondary'),
    teal: getThemeColor('--color-teal'),
  };
}
