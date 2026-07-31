/**
 * 通用空状态组件（ADR-013 · CLAUDE.md 第二原则组件复用）。
 *
 * 用于列表/详情页的 empty 分支，替代各页内联的空状态标记，确保
 * 空状态视觉与文案一致。
 */
interface EmptyStateProps {
  /** Emoji 或文本图标，留空则不渲染图标行。 */
  icon?: string;
  /** 主标题。 */
  title: string;
  /** 辅助描述。 */
  description?: string;
  /** 主操作按钮（可选）。 */
  action?: {
    label: string;
    onClick: () => void;
  };
}

export function EmptyState({ icon, title, description, action }: EmptyStateProps) {
  return (
    <div className="empty-state">
      {icon && <div className="mb-4 text-5xl opacity-40">{icon}</div>}
      <h2>{title}</h2>
      {description && <p>{description}</p>}
      {action && (
        <button className="btn btn-primary mt-4" onClick={action.onClick}>
          {action.label}
        </button>
      )}
    </div>
  );
}
