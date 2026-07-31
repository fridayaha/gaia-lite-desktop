import { useCallback, useEffect, useRef, useState } from 'react';

/**
 * 表单草稿自动保存到 localStorage。HCI 依据：尼尔森「用户可控与自由」+
 * 容错理论「恢复性容错」——多步向导填到一半刷新不丢失。
 *
 * 用法：
 *   const { data, set, clear, hasRestoredDraft } = useDraft('wizard:hr:obj', initial);
 *   // 重新打开时若检测到草稿，hasRestoredDraft=true，UI 可提示用户。
 */
export function useDraft<T>(
  key: string,
  initial: T,
): {
  data: T;
  set: (patch: Partial<T> | ((prev: T) => T)) => void;
  clear: () => void;
  hasRestoredDraft: boolean;
  dismissRestored: () => void;
} {
  // 惰性初始化：首次渲染即从 localStorage 恢复（避免 effect 级联渲染）。
  const [data, setData] = useState<T>(() => {
    try {
      const raw = localStorage.getItem(key);
      if (raw) return JSON.parse(raw) as T;
    } catch {
      /* ignore corrupt draft */
    }
    return initial;
  });
  const [hasRestoredDraft, setHasRestoredDraft] = useState(() => {
    try {
      return localStorage.getItem(key) !== null;
    } catch {
      return false;
    }
  });
  // 标记是否已首次保存过，避免恢复值被立刻回写（无意义但无害），主要用于跳过首帧。
  const savedOnce = useRef(false);

  // 数据变化时自动保存
  useEffect(() => {
    if (!savedOnce.current) {
      savedOnce.current = true;
      return;
    }
    try {
      localStorage.setItem(key, JSON.stringify(data));
    } catch {
      /* quota / private mode — 静默失败 */
    }
  }, [key, data]);

  const set = useCallback((patch: Partial<T> | ((prev: T) => T)) => {
    setData((prev) =>
      typeof patch === 'function' ? (patch as (p: T) => T)(prev) : { ...prev, ...patch },
    );
  }, []);

  const clear = useCallback(() => {
    try {
      localStorage.removeItem(key);
    } catch {
      /* ignore */
    }
    setHasRestoredDraft(false);
  }, [key]);

  const dismissRestored = useCallback(() => setHasRestoredDraft(false), []);

  return { data, set, clear, hasRestoredDraft, dismissRestored };
}
