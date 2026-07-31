import { useCallback, useRef, useState } from 'react';

/**
 * 统一管理异步操作的 loading / error / disabled 状态。
 *
 * HCI 依据：尼尔森「系统状态可见」+「反馈闭环」——
 * 任何写操作点击后必须有即时反馈，避免用户重复点击导致重复创建。
 *
 * 用法：
 *   const { loading, error, run } = useAsyncAction();
 *   <button className={cn('btn', loading && 'is-loading')} disabled={loading} onClick={() => run(asyncFn)}>
 *     {loading ? <><span className="btn-spinner" /> 处理中…</> : '提交'}
 *   </button>
 */
export interface UseAsyncActionReturn {
  loading: boolean;
  error: string | null;
  /** 执行异步函数；成功返回结果，失败把 error 写入 state 并返回 null。 */
  run: <T>(fn: () => Promise<T>) => Promise<T | null>;
  /** 手动清除错误。 */
  clearError: () => void;
}

export function useAsyncAction(): UseAsyncActionReturn {
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const mounted = useRef(true);

  const run = useCallback(async <T>(fn: () => Promise<T>): Promise<T | null> => {
    setLoading(true);
    setError(null);
    try {
      const result = await fn();
      if (mounted.current) setLoading(false);
      return result;
    } catch (err) {
      if (mounted.current) {
        setError(err instanceof Error ? err.message : String(err));
        setLoading(false);
      }
      return null;
    }
  }, []);

  const clearError = useCallback(() => setError(null), []);

  return { loading, error, run, clearError };
}
