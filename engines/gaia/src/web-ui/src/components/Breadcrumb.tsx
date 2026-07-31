import { Link } from 'react-router-dom';
import { cn } from '../lib/cn';

export interface Crumb {
  label: string;
  to?: string;
}

interface BreadcrumbProps {
  items: Crumb[];
  className?: string;
}

/**
 * 统一面包屑。HCI 依据：尼尔森「用户可控」+「系统状态可见」——
 * 深层页面给出返回路径与当前位置定位。
 */
export function Breadcrumb({ items, className }: BreadcrumbProps) {
  if (items.length === 0) return null;
  return (
    <nav aria-label="面包屑导航" className={cn('breadcrumb', className)}>
      {items.map((item, i) => {
        const isLast = i === items.length - 1;
        return (
          <span key={i} className="flex items-center gap-1.5">
            {i > 0 && <span className="crumb-sep">/</span>}
            {item.to && !isLast ? (
              <Link to={item.to}>{item.label}</Link>
            ) : (
              <span className={isLast ? 'crumb-current' : ''}>{item.label}</span>
            )}
          </span>
        );
      })}
    </nav>
  );
}
