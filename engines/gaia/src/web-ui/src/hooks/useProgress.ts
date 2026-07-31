import { useEffect, useState } from 'react';

export interface ProgressState {
  /** 当前已完成数。 */
  current: number;
  /** 总数。 */
  total: number;
  /** 可选文案，如"创建对象"。 */
  label?: string;
}

let listeners: Array<(s: ProgressState | null) => void> = [];
let currentState: ProgressState | null = null;

function emit(s: ProgressState | null) {
  currentState = s;
  for (const l of listeners) l(s);
}

/**
 * 全局长任务进度。供执行端调用 set/start/done，供 UI 端订阅渲染顶部进度条。
 *
 * HCI 依据：尼尔森「系统状态可见」——>3s 的批量操作必须有进度反馈。
 *
 * 用法（执行端）：
 *   const progress = useProgress();
 *   progress.start(items.length, '创建对象');
 *   for (const item of items) { await create(item); progress.tick(); }
 *   progress.done();
 *
 * 用法（UI 端，通常在 AppLayout 挂一次）：
 *   const { state } = useProgress();
 *   {state && <ProgressBar state={state} />}
 */
export function useProgress() {
  return {
    start(total: number, label?: string) {
      emit({ current: 0, total, label });
    },
    tick(n = 1) {
      if (!currentState) return;
      emit({ ...currentState, current: Math.min(currentState.current + n, currentState.total) });
    },
    set(current: number) {
      if (!currentState) return;
      emit({ ...currentState, current: Math.min(current, currentState.total) });
    },
    done() {
      emit(null);
    },
  };
}

/** 订阅全局进度状态（供 UI 渲染）。 */
export function useProgressState(): ProgressState | null {
  const [state, setState] = useState<ProgressState | null>(currentState);
  useEffect(() => {
    listeners.push(setState);
    return () => {
      listeners = listeners.filter((l) => l !== setState);
    };
  }, []);
  return state;
}
