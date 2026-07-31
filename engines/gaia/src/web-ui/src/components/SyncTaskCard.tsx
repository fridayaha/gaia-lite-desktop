import { useNavigate } from 'react-router-dom';
import { StatusBadge } from './StatusBadge';
import { cn } from '../lib/cn';
import type { SyncTask } from '../types';

interface SyncTaskCardProps {
  task: SyncTask;
  onStart?: () => void;
  onStop?: () => void;
  onDelete?: () => void;
  onClick?: () => void;
}

export function SyncTaskCard({ task, onStart, onStop, onDelete, onClick }: SyncTaskCardProps) {
  const navigate = useNavigate();

  // Display the SyncTask's real status as stored in PG. The backend's
  // refresh_sync_status reconciles this with SeaTunnel's job state, so we
  // no longer derive a fake "COMPLETED" here — that derivation was the
  // root cause of tasks showing "已完成" while SeaTunnel had never run.
  // full_snapshot one-shot jobs that have finished are persisted as
  // STOPPED by the backend; the UI shows that honestly.
  const displayStatus: string = task.status;
  const isScheduled = task.schedule?.interval_minutes != null || task.schedule?.cron != null;

  // Label for the sync mode badge
  const modeLabel = task.sync_mode === 'incremental' ? '增量' : '全量';
  const scheduleLabel = isScheduled ? '周期' : '一次性';

  return (
    <div className={cn('sync-task-item', onClick && 'cursor-pointer')} onClick={onClick}>
      <div className="sync-task-info">
        <strong className="font-mono text-sm">{task.api_name}</strong>
        <span className="badge bg-white/5 px-1.5 py-px text-[10px] text-text-muted">
          {scheduleLabel}
        </span>
        <StatusBadge
          status={displayStatus}
          labelMap={{
            RUNNING: '运行中',
            FINISHED: '已完成',
            STOPPED: '已停止',
            CANCELED: '已取消',
            DRAFT: '草稿',
            FAILED: '失败',
          }}
        />
        <span className="text-[11px] text-text-muted">
          {modeLabel} →{' '}
          <code
            className="cursor-pointer font-mono text-[10px] text-accent-text underline decoration-dotted"
            onClick={(e) => {
              e.stopPropagation();
              navigate(`/data/datasets/${task.target_dataset_api_name}`);
            }}
          >
            {task.target_dataset_api_name}
          </code>
        </span>
        {task.last_run_at && (
          <span className="text-[10px] text-text-muted">
            上次: {new Date(task.last_run_at).toLocaleString()}
          </span>
        )}
        {task.schedule?.interval_minutes != null && (
          <span className="text-[10px] text-text-muted">
            每 {String(task.schedule.interval_minutes)} 分钟
          </span>
        )}
        {task.schedule?.cron != null && (
          <span className="text-[10px] text-text-muted">cron: {String(task.schedule.cron)}</span>
        )}
      </div>
      <div className="flex shrink-0 items-center gap-1">
        {displayStatus !== 'RUNNING' && onStart && (
          <button
            className="btn btn-xs btn-primary"
            onClick={(e) => {
              e.stopPropagation();
              onStart();
            }}
          >
            启动
          </button>
        )}
        {displayStatus === 'RUNNING' && onStop && (
          <button
            className="btn btn-xs"
            onClick={(e) => {
              e.stopPropagation();
              onStop();
            }}
          >
            停止
          </button>
        )}
        {onDelete && (
          <>
            <span className="mx-0.5 h-4 w-px bg-border" aria-hidden="true" />
            <button
              className="btn btn-xs border-error text-error"
              aria-label={`删除同步任务 ${task.api_name}`}
              title="删除（高危，需二次确认）"
              onClick={(e) => {
                e.stopPropagation();
                onDelete();
              }}
            >
              删除
            </button>
          </>
        )}
      </div>
    </div>
  );
}
