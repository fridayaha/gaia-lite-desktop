/** Unified status badge — replaces scattered inline status styling. */
import { cn } from '../lib/cn';

export interface StatusBadgeProps {
  status: string;
  labelMap?: Record<string, string>;
  className?: string;
}

const STATUS_CLASSES: Record<string, string> = {
  // Connection
  CONNECTED: 'sb-success',
  DISCONNECTED: 'sb-muted',
  ERROR: 'sb-error',
  // Sync
  RUNNING: 'sb-success',
  FINISHED: 'sb-success',
  STOPPED: 'sb-muted',
  CANCELED: 'sb-warning',
  FAILED: 'sb-error',
  DRAFT: 'sb-warning',
  // Object
  ACTIVE: 'sb-success',
  ENDORSED: 'sb-success',
  EXPERIMENTAL: 'sb-warning',
  DEPRECATED: 'sb-muted',
  // Dataset type (用户心智维度：托管/虚拟/加工)
  managed: 'sb-muted',
  virtual: 'sb-success',
  transform: 'sb-warning',
};

const STATUS_LABELS: Record<string, string> = {
  CONNECTED: '已连接',
  DISCONNECTED: '未连接',
  ERROR: '异常',
  RUNNING: '运行中',
  FINISHED: '已完成',
  STOPPED: '已停止',
  CANCELED: '已取消',
  FAILED: '失败',
  DRAFT: '草稿',
  ACTIVE: '活跃',
  ENDORSED: '已验证',
  EXPERIMENTAL: '实验',
  DEPRECATED: '废弃',
};

export function StatusBadge({ status, labelMap, className }: StatusBadgeProps) {
  const cls = STATUS_CLASSES[status] || 'sb-muted';
  const label = labelMap?.[status] || STATUS_LABELS[status] || status;

  return <span className={cn('status-badge', cls, className)}>{label}</span>;
}
