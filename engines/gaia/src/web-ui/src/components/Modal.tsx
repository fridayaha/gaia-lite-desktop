import { Modal as AriaModal, ModalOverlay, Dialog as AriaDialog } from 'react-aria-components';
import { cn } from '../lib/cn';

interface ModalProps {
  open: boolean;
  onClose: () => void;
  children: React.ReactNode;
  /** 自定义遮罩 class（如定位/背景）。 */
  overlayClassName?: string;
  /** 自定义面板 class（如宽度 max-w-[560px]）。默认 min-w-[400px] max-w-[600px]。 */
  panelClassName?: string;
  /** 自定义面板 inline style（如精确宽度/高度）。 */
  style?: React.CSSProperties;
  /** 是否点击遮罩关闭，默认 true。 */
  closeOnOverlay?: boolean;
  /** aria-label，用于屏幕阅读器。 */
  ariaLabel?: string;
}

/**
 * 统一 Modal 底座，基于 React Aria Components（ADR-013 Phase 2）。
 *
 * React Aria 的 ModalOverlay + Modal + Dialog 提供：
 *  - 焦点陷阱（focus trap）+ 焦点回归触发元素
 *  - ESC 关闭、iOS body 滚动锁定
 *  - aria-modal、屏幕阅读器播报
 *
 * 对外保持原 API（open/onClose/children/overlayClassName/closeOnOverlay/
 * ariaLabel），调用点零感知。`isDismissable` 控制 ESC + 点击遮罩关闭。
 * 内部保留 `.dialog` class 以兼容既有后代样式（h2 / dialog-actions）。
 */
export function Modal({
  open,
  onClose,
  children,
  overlayClassName,
  panelClassName,
  style,
  closeOnOverlay = true,
  ariaLabel,
}: ModalProps) {
  if (!open) return null;

  return (
    <ModalOverlay
      // 由父级受控：!open 时本组件直接 return null，故这里恒为 true。
      isOpen
      // isDismissable: ESC + 点击遮罩关闭（React Aria 标准行为）
      isDismissable={closeOnOverlay}
      isKeyboardDismissDisabled={!closeOnOverlay}
      onOpenChange={(isOpen) => {
        if (!isOpen) onClose();
      }}
      className={cn(
        'fixed inset-0 z-[var(--z-modal)] flex items-center justify-center bg-black/60',
        overlayClassName,
      )}
    >
      <AriaModal
        style={style}
        className={cn(
          // 保留 .dialog class 兼容既有后代样式（h2 / dialog-actions）
          'dialog min-w-[400px] max-w-[600px] rounded-lg border border-border bg-surface p-6 outline-none',
          panelClassName,
        )}
      >
        <AriaDialog aria-label={ariaLabel} className="outline-none">
          {children}
        </AriaDialog>
      </AriaModal>
    </ModalOverlay>
  );
}
