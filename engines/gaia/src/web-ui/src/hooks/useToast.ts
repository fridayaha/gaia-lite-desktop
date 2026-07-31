import { useCallback, useState } from 'react';

export type ToastType = 'success' | 'error' | 'info';

export interface Toast {
  id: number;
  message: string;
  type: ToastType;
}

export interface UseToastReturn {
  toast: Toast | null;
  show: (message: string, type?: ToastType) => void;
  dismiss: () => void;
}

/**
 * 类型化 Toast。替代各页面散落的 `const [toast, setToast] = useState<string|null>`。
 * 单条 Toast（后到先得），点击/3.5s 自动消失。
 */
export function useToast(): UseToastReturn {
  const [toast, setToast] = useState<Toast | null>(null);

  const show = useCallback((message: string, type: ToastType = 'info') => {
    const id = Date.now();
    setToast({ id, message, type });
    setTimeout(() => {
      setToast((current) => (current?.id === id ? null : current));
    }, 3500);
  }, []);

  const dismiss = useCallback(() => setToast(null), []);

  return { toast, show, dismiss };
}
