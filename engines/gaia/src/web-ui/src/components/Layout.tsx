import { useState, useEffect } from 'react';
import { Outlet, useNavigate, useLocation } from 'react-router-dom';
import { ThemeToggle } from './ThemeToggle';
import { ProgressBar } from './ProgressBar';
import { UserMenu } from './UserMenu';
import { cn } from '../lib/cn';
import { getDeploymentInfo } from '../api/permission';

/** 页面可通过声明 fullBleed=true 取消 main-content 默认内边距（三栏布局自管间距）。 */
export interface LayoutOutletContext {
  setFullBleed: (v: boolean) => void;
}

interface RailItem {
  id: string;
  label: string;
  shortLabel: string;
  icon: string;
  hint: string;
  path: string;
  /** Emoji size override — some emoji render too small (e.g. ⚡). */
  emojiSize?: string;
  /** 二级子项（可展开分组）。 */
  children?: RailItem[];
}

// B6: lite 版隐藏图探索菜单（后端图遍历走 Neo4j，lite 砍掉 → 页面空壳）。
// 用构建期常量 __EDITION__ 过滤，lite 下 explore 子项不进 RAIL_ITEMS。
const RAIL_ITEMS: RailItem[] = (
  [
  {
    id: 'ontology',
    label: '本体构建',
    shortLabel: '本体',
    icon: '🏗️',
    hint: '① 定义业务对象、关系与可执行动作',
    path: '/',
    children: [
      {
        id: 'ontology-modeling',
        label: '本体建模',
        shortLabel: '建模',
        icon: '🧠',
        hint: '定义业务概念与数据映射',
        path: '/',
      },
      {
        id: 'actions',
        label: '动作管理',
        shortLabel: '动作',
        icon: '⚡',
        hint: '定义AI可执行的操作与业务规则',
        path: '/actions',
        emojiSize: 'text-lg',
      },
      {
        id: 'explore',
        label: '图探索',
        shortLabel: '探索',
        icon: '🔍',
        hint: '关联推理与时空分析',
        path: '/explore',
      },
    ],
  },
  {
    id: 'data',
    label: '数据集成',
    shortLabel: '数据',
    icon: '🔗',
    hint: '② 连接外部数据源并管理数据集',
    path: '/data/sources',
    children: [
      {
        id: 'data-sources',
        label: '数据源',
        shortLabel: '源',
        icon: '📡',
        hint: '外部系统连接与同步',
        path: '/data/sources',
      },
      {
        id: 'data-datasets',
        label: '数据集',
        shortLabel: '集',
        icon: '📦',
        hint: '已落地数据资产',
        path: '/data/datasets',
      },
    ],
  },
  {
    id: 'pipelines',
    label: '管道编排',
    shortLabel: '管道',
    icon: '🔧',
    hint: '③ 可视化数据管道编排与构建',
    path: '/pipelines',
  },
  {
    id: 'ops',
    label: '运营看板',
    shortLabel: '运营',
    icon: '📊',
    hint: '④ 资源概览与运行监控',
    path: '/ops',
  },
  // 设置（design §8.1 渐进式披露）：权限管理降级为二级入口，
  // 默认折叠。业务用户不需要日常进入。多租户模式下才显示三层容器管理。
  {
    id: 'settings',
    label: '设置',
    shortLabel: '设置',
    icon: '⚙️',
    hint: '权限管理、审计与系统配置',
    path: '/authz/check',
    children: [
      {
        id: 'identity',
        label: '身份管理',
        shortLabel: '身份',
        icon: '👥',
        hint: '用户与用户组管理、角色授予',
        path: '/authz/identity',
      },
      {
        id: 'authz-check',
        label: '权限调试',
        shortLabel: '调试',
        icon: '🔎',
        hint: '五层校验状态可视化',
        path: '/authz/check',
      },
      {
        id: 'authz-markings',
        label: '标记管理',
        shortLabel: '标记',
        icon: '🏷️',
        hint: 'MAC 标记分类与值',
        path: '/authz/markings',
      },
      {
        id: 'authz-requests',
        label: '权限申请',
        shortLabel: '申请',
        icon: '✋',
        hint: 'JIT 自助申请与审批',
        path: '/authz/requests',
      },
      {
        id: 'authz-audit',
        label: '审计日志',
        shortLabel: '审计',
        icon: '📜',
        hint: '权限决策的追加写入日志',
        path: '/authz/audit',
      },
    ],
  },
  ] as RailItem[]
).map((item) =>
  // lite 版过滤掉图探索子项（explore id）+ 整个图探索顶层项。
  __EDITION__ === 'lite'
    ? {
        ...item,
        children: item.children?.filter((c) => c.id !== 'explore'),
      }
    : item
);

/** 路径 → 面包屑片段（用于 titlebar 显示当前位置）。 */
function resolveCrumb(pathname: string): string[] {
  if (pathname === '/') return ['本体构建', '本体建模'];
  if (pathname.startsWith('/actions')) return ['本体构建', '动作管理', ...(pathname.split('/').filter(Boolean).slice(1))];
  if (pathname.startsWith('/explore')) return ['本体构建', '图探索', ...(pathname.split('/').filter(Boolean).slice(1))];
  if (pathname.startsWith('/pipelines')) {
    const segs = pathname.split('/').filter(Boolean);
    if (segs.length === 1) return ['管道编排', '管道列表'];
    if (segs[1] === 'new') return ['管道编排', '新建管道'];
    return ['管道编排', '管道编辑'];
  }
  if (pathname.startsWith('/data')) {
    const segs = pathname.split('/').filter(Boolean);
    if (segs.length === 1) return ['数据集成', '数据源'];
    if (segs[1] === 'sources') return segs.length === 2 ? ['数据集成', '数据源'] : ['数据集成', '数据源详情'];
    if (segs[1] === 'datasets') return segs.length === 2 ? ['数据集成', '数据集'] : ['数据集成', '数据集详情'];
    if (segs[1] === 'syncs') return ['数据集成', '同步任务详情'];
    return ['数据集成', ...segs.slice(1)];
  }
  if (pathname.startsWith('/ops')) return ['运营看板', ...(pathname.split('/').filter(Boolean).slice(1))];
  if (pathname.startsWith('/authz')) return ['设置', ...(pathname.split('/').filter(Boolean).slice(1))];
  if (pathname.startsWith('/settings')) return ['设置', ...(pathname.split('/').filter(Boolean).slice(1))];
  return [];
}

export function AppLayout() {
  const navigate = useNavigate();
  const location = useLocation();
  const [fullBleed, setFullBleed] = useState(false);
  const [isMultiTenant, setIsMultiTenant] = useState(false);

  // 加载部署信息（多租户信号，design §8.1）。
  useEffect(() => {
    getDeploymentInfo()
      .then((info) => setIsMultiTenant(info.is_multi_tenant))
      .catch(() => setIsMultiTenant(false));
  }, []);

  // 动态 rail items：多租户模式下在「设置」分组下追加三层容器管理。
  const railItems: RailItem[] = isMultiTenant
    ? RAIL_ITEMS.map((it) =>
        it.id === 'settings'
          ? {
              ...it,
              children: [
                ...(it.children ?? []),
                { id: 'orgs', label: '组织管理', shortLabel: '组织', icon: '🏢', hint: '多租户隔离', path: '/settings/organizations' },
              ],
            }
          : it,
      )
    : RAIL_ITEMS;

  // 分组展开态记忆（localStorage 持久化，默认展开数据分组）
  const [expandedGroups, setExpandedGroups] = useState<Record<string, boolean>>(() => {
    try {
      const saved = localStorage.getItem('rail-expanded-groups');
      if (saved) return JSON.parse(saved) as Record<string, boolean>;
    } catch {
      /* ignore */
    }
    return { ontology: true }; // 本体构建是核心工作区，默认展开
  });

  function toggleGroup(id: string) {
    setExpandedGroups((prev) => {
      const next = { ...prev, [id]: !prev[id] };
      try {
        localStorage.setItem('rail-expanded-groups', JSON.stringify(next));
      } catch {
        /* ignore */
      }
      return next;
    });
  }

  const currentPanel =
    railItems.find((it) => {
      if (it.children?.length) {
        // 分组有子项：匹配任一子项的 path
        return it.children.some((child) =>
          child.path === '/' ? location.pathname === '/' : location.pathname.startsWith(child.path),
        );
      }
      return location.pathname.startsWith('/' + it.id);
    })?.id ?? 'ontology';

  // 子项 active 判断
  function isChildActive(child: RailItem): boolean {
    return location.pathname === child.path || location.pathname.startsWith(child.path + '/');
  }

  const handleRailClick = (item: RailItem) => {
    // 有子项的分组：点击切换展开（而非直接跳转，避免与子项冲突）
    if (item.children && item.children.length > 0) {
      toggleGroup(item.id);
      return;
    }
    navigate(item.path);
  };

  const handleChildClick = (child: RailItem) => navigate(child.path);

  const crumbs = resolveCrumb(location.pathname);

  return (
    <div className="app-layout">
      <ProgressBar />

      {/* Titlebar */}
      <header className="app-titlebar">
        <span className="app-titlebar-logo">
          <span aria-hidden="true">▲</span>
          <span>Gaia</span>
        </span>
        {crumbs.length > 0 && (
          <span className="app-titlebar-crumb" aria-label="当前位置">
            {crumbs.map((c, i) => (
              <span key={i} className="flex items-center gap-1.5">
                {i > 0 && <span className="crumb-sep">/</span>}
                <span className={i === crumbs.length - 1 ? 'crumb-current' : ''}>{c}</span>
              </span>
            ))}
          </span>
        )}
        <span className="app-titlebar-spacer" />
        <UserMenu />
        <span className="text-[11px] leading-none text-text-muted self-center">本体建模平台</span>
      </header>

      <div className="app-body">
        {/* Rail —— 图标 + 常显文字标签（识别优于记忆），带子项的分组可展开 */}
        <nav className="rail" aria-label="主导航">
          {railItems.map((item) => {
            const hasChildren = item.children && item.children.length > 0;
            const isExpanded = hasChildren && expandedGroups[item.id];
            return (
              <div key={item.id} className="flex flex-col">
                <button
                  className={cn(
                    'rail-btn',
                    currentPanel === item.id && 'active',
                    hasChildren && isExpanded && 'group-expanded',
                  )}
                  onClick={() => handleRailClick(item)}
                  aria-label={item.label}
                  aria-expanded={hasChildren ? isExpanded : undefined}
                  aria-current={currentPanel === item.id && !hasChildren ? 'page' : undefined}
                  title={`${item.label} · ${item.hint}`}
                >
                  <span className={cn('rail-btn-icon', item.emojiSize)} aria-hidden="true">
                    {item.icon}
                  </span>
                  <span className="rail-btn-label">{item.shortLabel}</span>
                  {hasChildren && (
                    <span
                      className={cn(
                        'rail-group-arrow text-[8px] text-text-muted transition-transform',
                        isExpanded && 'rotate-90',
                      )}
                      aria-hidden="true"
                    >
                      ▶
                    </span>
                  )}
                </button>
                {/* 子项（展开时显示） */}
                {hasChildren && isExpanded && (
                  <div className="rail-subitems">
                    {item.children!.map((child) => (
                      <button
                        key={child.id}
                        className={cn('rail-subitem', isChildActive(child) && 'active')}
                        onClick={() => handleChildClick(child)}
                        aria-label={child.label}
                        aria-current={isChildActive(child) ? 'page' : undefined}
                        title={`${child.label} · ${child.hint}`}
                      >
                        <span className={cn('rail-subitem-icon', child.emojiSize)} aria-hidden="true">
                          {child.icon}
                        </span>
                        <span className="rail-subitem-label">{child.label}</span>
                      </button>
                    ))}
                  </div>
                )}
              </div>
            );
          })}
          <span className="rail-spacer" />
          <ThemeToggle />
        </nav>

        {/* Main */}
        <main className={cn('main-content', fullBleed && 'full-bleed')} data-panel={currentPanel}>
          <Outlet context={{ setFullBleed } satisfies LayoutOutletContext} />
        </main>
      </div>
    </div>
  );
}
