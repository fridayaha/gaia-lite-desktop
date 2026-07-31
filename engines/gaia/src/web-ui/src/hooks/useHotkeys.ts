import { useEffect, useRef } from 'react';

export type KeyHandler = (e: KeyboardEvent) => void;

export interface HotkeyDef {
  /** 触发键，如 'n'、'/'、'Escape'、'1'。大小写不敏感（字母）。 */
  key: string;
  /** 是否需要组合键。 */
  ctrl?: boolean;
  meta?: boolean;
  shift?: boolean;
  alt?: boolean;
  handler: KeyHandler;
  /** 是否在输入框内也触发（默认 false，输入态屏蔽单字符快捷键）。 */
  allowInInput?: boolean;
}

function isEditableTarget(e: KeyboardEvent): boolean {
  const t = e.target as HTMLElement | null;
  if (!t) return false;
  const tag = t.tagName;
  return tag === 'INPUT' || tag === 'TEXTAREA' || tag === 'SELECT' || t.isContentEditable;
}

/**
 * 注册一组快捷键。HCI 依据：尼尔森「灵活高效（熟手）」——
 * 高频操作提供快捷键，降低熟手操作成本。
 *
 * 自动处理：组件卸载时解绑；输入态默认屏蔽（除非 allowInInput）。
 */
export function useHotkeys(hotkeys: HotkeyDef[]): void {
  const ref = useRef(hotkeys);
  useEffect(() => {
    ref.current = hotkeys;
  });

  useEffect(() => {
    const handler = (e: KeyboardEvent) => {
      const defs = ref.current;
      for (const hk of defs) {
        if (hk.ctrl !== e.ctrlKey) continue;
        if (hk.meta !== e.metaKey) continue;
        if (hk.shift !== e.shiftKey) continue;
        if (hk.alt !== e.altKey) continue;
        const key = e.key.toLowerCase();
        if (hk.key.toLowerCase() !== key) continue;
        if (!hk.allowInInput && isEditableTarget(e)) continue;
        e.preventDefault();
        hk.handler(e);
        break;
      }
    };
    document.addEventListener('keydown', handler);
    return () => document.removeEventListener('keydown', handler);
  }, []);
}
