import { useProgressState } from '../hooks/useProgress';

/**
 * 全局顶部进度条。挂载一次（在 AppLayout），订阅 useProgressState。
 * 长任务（批量创建等）调用 useProgress().start/tick/done 控制。
 */
export function ProgressBar() {
  const state = useProgressState();
  if (!state) return null;
  const pct = state.total > 0 ? Math.round((state.current / state.total) * 100) : 0;
  return (
    <>
      <div
        className="progress-bar"
        role="progressbar"
        aria-valuenow={pct}
        aria-valuemin={0}
        aria-valuemax={100}
      >
        <div className="progress-bar-fill" style={{ width: `${pct}%` }} />
      </div>
      <div className="progress-bar-label">
        {state.label || '处理中'} {state.current}/{state.total}
      </div>
    </>
  );
}
