import { useTheme } from '../hooks/useTheme';

/**
 * 亮/暗主题切换按钮，放在顶栏。
 */
export function ThemeToggle() {
  const { theme, toggleTheme } = useTheme();
  const isDark = theme === 'dark';

  return (
    <button
      type="button"
      className="rail-btn"
      onClick={toggleTheme}
      aria-label={isDark ? '切换到亮色主题' : '切换到暗色主题'}
      aria-pressed={!isDark}
      title={isDark ? '亮色主题' : '暗色主题'}
    >
      <span className="rail-btn-icon" aria-hidden="true">{isDark ? '☀️' : '🌙'}</span>
      <span className="rail-btn-label">{isDark ? '亮色' : '暗色'}</span>
    </button>
  );
}
