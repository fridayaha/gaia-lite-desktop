import { cn } from '../lib/cn';
import type { Toast } from '../hooks/useToast';

const BORDER_BY_TYPE: Record<Toast['type'], string> = {
  success: 'toast-success',
  error: 'toast-error',
  info: 'toast-info',
};

const ICON_BY_TYPE: Record<Toast['type'], string> = {
  success: '✓',
  error: '⚠',
  info: 'ℹ',
};

const LABEL_BY_TYPE: Record<Toast['type'], string> = {
  success: '成功',
  error: '错误',
  info: '提示',
};

/** 类型化 Toast 渲染。配合 useToast 使用。带 aria-live 供屏幕阅读器播报。 */
export function ToastView({ toast, onDismiss }: { toast: Toast | null; onDismiss: () => void }) {
  if (!toast) return null;
  return (
    <div
      className={cn('toast', BORDER_BY_TYPE[toast.type])}
      role={toast.type === 'error' ? 'alert' : 'status'}
      aria-live={toast.type === 'error' ? 'assertive' : 'polite'}
      onClick={onDismiss}
    >
      <span aria-hidden="true" className="mr-1.5">
        {ICON_BY_TYPE[toast.type]}
      </span>
      <span className="sr-only">{LABEL_BY_TYPE[toast.type]}：</span>
      {toast.message}
    </div>
  );
}
