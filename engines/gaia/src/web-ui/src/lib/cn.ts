import { clsx, type ClassValue } from 'clsx';
import { twMerge } from 'tailwind-merge';

/**
 * 合并 className：clsx 处理条件，tailwind-merge 解决 Tailwind 类冲突。
 *
 * @example cn('px-2 py-1', isActive && 'bg-accent text-bg', 'px-4') // → 'py-1 bg-accent text-bg px-4'
 */
export function cn(...inputs: ClassValue[]): string {
  return twMerge(clsx(inputs));
}
