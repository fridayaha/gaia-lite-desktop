/**
 * 分页器（ADR-013 · ui 原语）。
 *
 * 轻量前端分页：父组件持有 page + pageSize，切片 rows 后用本组件展示
 * 导航。不耦合数据获取——适用于已全量加载的列表（如 datasets 全量
 * 拉取后切片展示）。后端分页场景应自行管理 total/page 状态后复用本组件。
 */
import { cn } from '../../lib/cn';

interface PaginationProps {
  /** 当前页（1-based）。 */
  page: number;
  pageSize: number;
  /** 总条数。 */
  total: number;
  onChange: (page: number) => void;
  onPageSizeChange?: (size: number) => void;
  /** 可选 pageSize 选项，不传则不显示切换器。 */
  pageSizeOptions?: number[];
  className?: string;
}

export function Pagination({
  page,
  pageSize,
  total,
  onChange,
  onPageSizeChange,
  pageSizeOptions,
  className,
}: PaginationProps) {
  const totalPages = Math.max(1, Math.ceil(total / pageSize));
  const cur = Math.min(page, totalPages);
  const start = total === 0 ? 0 : (cur - 1) * pageSize + 1;
  const end = Math.min(cur * pageSize, total);

  // 生成页码窗口：始终显示首页/末页，中间窗口围绕当前页
  const pages: (number | '…')[] = [];
  const window = 1; // 当前页左右各 1
  for (let i = 1; i <= totalPages; i++) {
    if (i === 1 || i === totalPages || (i >= cur - window && i <= cur + window)) {
      pages.push(i);
    } else if (pages[pages.length - 1] !== '…') {
      pages.push('…');
    }
  }

  const btn = (active: boolean) =>
    cn(
      'min-w-[28px] rounded px-2 py-0.5 text-[11px] transition-colors',
      active
        ? 'bg-[var(--accent-bg)] text-accent-text'
        : 'text-text-secondary hover:bg-white/[0.04]',
    );

  return (
    <div className={cn('flex items-center gap-2 text-[11px] text-text-muted', className)}>
      <span>
        {total === 0 ? '共 0 条' : `${start}-${end} / 共 ${total} 条`}
      </span>
      <div className="flex items-center gap-0.5">
        <button
          className={btn(false)}
          disabled={cur <= 1}
          onClick={() => onChange(cur - 1)}
          aria-label="上一页"
        >
          ‹
        </button>
        {pages.map((p, i) =>
          p === '…' ? (
            <span key={`ellipsis-${i}`} className="px-1 text-text-muted">
              …
            </span>
          ) : (
            <button
              key={p}
              className={btn(p === cur)}
              onClick={() => onChange(p)}
              aria-current={p === cur ? 'page' : undefined}
            >
              {p}
            </button>
          ),
        )}
        <button
          className={btn(false)}
          disabled={cur >= totalPages}
          onClick={() => onChange(cur + 1)}
          aria-label="下一页"
        >
          ›
        </button>
      </div>
      {onPageSizeChange && pageSizeOptions && (
        <select
          className="form-input px-1 py-0.5 text-[11px]"
          value={pageSize}
          onChange={(e) => onPageSizeChange(Number(e.target.value))}
          aria-label="每页条数"
        >
          {pageSizeOptions.map((n) => (
            <option key={n} value={n}>
              {n} 条/页
            </option>
          ))}
        </select>
      )}
    </div>
  );
}
