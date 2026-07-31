import { cn } from '../lib/cn';

interface SkeletonProps {
  className?: string;
}

/** 单行骨架。 */
export function SkeletonLine({ className, short }: { className?: string; short?: boolean }) {
  return <div className={cn('skeleton skeleton-line', short && 'short', className)} />;
}

/** 块骨架（卡片/段落占位）。 */
export function SkeletonBlock({ className }: SkeletonProps) {
  return <div className={cn('skeleton skeleton-block', className)} />;
}

/** 列表骨架（N 行占位）。 */
export function SkeletonList({ rows = 3 }: { rows?: number }) {
  return (
    <div className="flex flex-col gap-2">
      {Array.from({ length: rows }).map((_, i) => (
        <SkeletonLine key={i} short={i % 3 === 2} />
      ))}
    </div>
  );
}

/** 详情面板结构化骨架。 */
export function SkeletonDetail() {
  return (
    <div className="flex flex-col gap-3 p-4">
      <SkeletonLine short />
      <div className="mt-2 flex flex-col gap-2">
        <SkeletonLine />
        <SkeletonLine />
        <SkeletonLine short />
      </div>
      <div className="mt-2 flex flex-col gap-1.5">
        <SkeletonLine />
        <SkeletonLine short />
        <SkeletonLine />
      </div>
    </div>
  );
}
