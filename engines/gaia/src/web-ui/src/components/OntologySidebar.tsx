import { useState } from 'react';
import type { Ontology } from '../types';
import { cn } from '../lib/cn';

interface SidebarProps {
  ontologies: Ontology[];
  selectedOntology: string | null;
  onSelectOntology: (name: string) => void;
  onCreateOntology: () => void;
  /** 本体生命周期操作（资源管理，design 第二步职责分离）。 */
  onDeprecateOntology?: (apiName: string) => void;
  onRestoreOntology?: (apiName: string) => void;
  onDeleteOntology?: (apiName: string, displayName: string) => void;
  /** 权限决策（ship-the-decision，控制菜单项显隐）。 */
  decisions?: Record<string, { allowedActions: string[]; disabledReasons: Record<string, string> }>;
  loading?: boolean;
  /** 受控折叠状态。由父级（OntologyWorkspace）统一管理，
   *  以便与 AiAssistantDock 联动：dock 展开时自动收起本栏。 */
  collapsed: boolean;
  onCollapsedChange: (collapsed: boolean) => void;
}

const COLLAPSED_WIDTH = 44;

export function OntologySidebar({
  ontologies,
  selectedOntology,
  onSelectOntology,
  onCreateOntology,
  onDeprecateOntology,
  onRestoreOntology,
  onDeleteOntology,
  decisions,
  loading,
  collapsed,
  onCollapsedChange,
}: SidebarProps) {
  const [menuOpen, setMenuOpen] = useState<string | null>(null);
  const toggle = () => onCollapsedChange(!collapsed);

  // ── 折叠态：贴边图标条 ──
  if (collapsed) {
    return (
      <aside
        className="sidebar sidebar--collapsed flex shrink-0 flex-col items-center gap-2 border-r border-border bg-sidebar py-2"
        style={{ width: COLLAPSED_WIDTH }}
        aria-label="本体列表（已折叠）"
      >
        <button
          type="button"
          className="flex h-7 w-7 items-center justify-center rounded text-text-muted hover:bg-[var(--accent-bg)] hover:text-text"
          onClick={toggle}
          title="展开本体列表"
          aria-label="展开本体列表"
        >
          ›
        </button>
        <button
          type="button"
          className="flex h-7 w-7 items-center justify-center rounded text-base font-bold text-accent-text hover:bg-[var(--accent-bg)]"
          onClick={onCreateOntology}
          title="新建本体"
          aria-label="新建本体"
        >
          +
        </button>
        <div className="h-px w-6 bg-border" aria-hidden="true" />
        {/* 本体缩略：首字图标，选中态高亮，点击切换/展开 */}
        <div className="flex min-h-0 flex-1 flex-col items-center gap-1 overflow-y-auto">
          {loading
            ? Array.from({ length: 3 }).map((_, i) => (
                <div key={i} className="h-7 w-7 animate-pulse rounded bg-[var(--accent-bg)]" />
              ))
            : ontologies.map((onto) => {
                const isActive = selectedOntology === onto.api_name;
                const deprecated = onto.status === 'DEPRECATED';
                return (
                  <button
                    key={onto.id}
                    type="button"
                    onClick={() => onSelectOntology(onto.api_name)}
                    onDoubleClick={toggle}
                    title={`${deprecated ? '⚠ ' : ''}${onto.display_name}（双击展开列表）`}
                    aria-label={onto.display_name}
                    aria-selected={isActive}
                    className={cn(
                      'flex h-7 w-7 items-center justify-center rounded text-xs font-semibold transition-colors',
                      isActive
                        ? 'bg-accent text-accent-text'
                        : 'text-text-secondary hover:bg-[var(--accent-bg)] hover:text-text',
                      deprecated && 'opacity-60',
                    )}
                  >
                    {onto.display_name.slice(0, 1)}
                  </button>
                );
              })}
        </div>
      </aside>
    );
  }

  // ── 展开态 ──
  return (
    <aside className="sidebar" aria-label="本体列表">
      <div className="sidebar-header">
        我的本体
        <div className="float-right flex items-center gap-1">
          <button
            onClick={onCreateOntology}
            className="cursor-pointer border-none bg-none text-base font-bold text-accent-text"
            aria-label="新建本体"
            title="新建本体"
          >
            +
          </button>
          <button
            type="button"
            onClick={toggle}
            className="flex h-5 w-5 cursor-pointer items-center justify-center border-none bg-none text-xs text-text-muted hover:text-text"
            title="收起本体列表"
            aria-label="收起本体列表"
          >
            ‹
          </button>
        </div>
      </div>

      <div className="sidebar-list" role="listbox" aria-label="本体">
        {loading
          ? Array.from({ length: 3 }).map((_, i) => (
              <div key={i} className="px-3 py-2">
                <div className="skeleton skeleton-line short" />
              </div>
            ))
          : ontologies.map((onto) => {
              const isActive = selectedOntology === onto.api_name;
              const deprecated = onto.status === 'DEPRECATED';
              const hasMenu = !!(onDeprecateOntology || onRestoreOntology || onDeleteOntology);
              return (
                <div
                  key={onto.id}
                  className={cn(
                    'sidebar-item-row group relative flex items-center',
                    isActive && 'active',
                    deprecated && 'opacity-60',
                  )}
                >
                  <button
                    type="button"
                    role="option"
                    aria-selected={isActive}
                    className="flex-1 truncate text-left"
                    onClick={() => onSelectOntology(onto.api_name)}
                    title={deprecated ? '已弃用（Deprecate）' : onto.display_name}
                  >
                    <span className="truncate">
                      {deprecated && <span aria-hidden="true">⚠ </span>}
                      {onto.display_name}
                    </span>
                  </button>
                  <span className={cn('badge', !isActive && 'opacity-60')}>
                    {onto.object_types_count}
                  </span>
                  {hasMenu && (
                    <button
                      type="button"
                      className="ml-1 flex h-5 w-5 shrink-0 items-center justify-center rounded text-text-muted opacity-0 hover:bg-[var(--accent-bg)] hover:text-text group-hover:opacity-100"
                      onClick={(e) => {
                        e.stopPropagation();
                        setMenuOpen(menuOpen === onto.id ? null : onto.id);
                      }}
                      title="本体管理"
                      aria-label={`${onto.display_name} 管理`}
                    >
                      ⋯
                    </button>
                  )}
                  {menuOpen === onto.id && (
                    <div className="absolute right-0 top-full z-20 mt-0.5 min-w-[120px] rounded-md border border-border bg-sidebar py-1 shadow-lg">
                      {deprecated && onRestoreOntology && (decisions?.[onto.api_name]?.allowedActions.includes('ontology:edit') ?? true) && (
                        <button
                          className="block w-full px-3 py-1.5 text-left text-xs hover:bg-[var(--accent-bg)]"
                          onClick={() => {
                            onRestoreOntology(onto.api_name);
                            setMenuOpen(null);
                          }}
                        >
                          恢复
                        </button>
                      )}
                      {!deprecated && onDeprecateOntology && (decisions?.[onto.api_name]?.allowedActions.includes('ontology:edit') ?? true) && (
                        <button
                          className="block w-full px-3 py-1.5 text-left text-xs hover:bg-[var(--accent-bg)]"
                          onClick={() => {
                            onDeprecateOntology(onto.api_name);
                            setMenuOpen(null);
                          }}
                        >
                          弃用
                        </button>
                      )}
                      {onDeleteOntology && (decisions?.[onto.api_name]?.allowedActions.includes('ontology:delete') ?? true) && (
                        <button
                          className="block w-full px-3 py-1.5 text-left text-xs text-error hover:bg-error/10"
                          onClick={() => {
                            onDeleteOntology(onto.api_name, onto.display_name);
                            setMenuOpen(null);
                          }}
                        >
                          删除
                        </button>
                      )}
                    </div>
                  )}
                </div>
              );
            })}

        {!loading && ontologies.length === 0 && (
          <div className="p-4 text-center text-xs text-text-muted">还没有本体，点击 + 创建</div>
        )}
      </div>
    </aside>
  );
}
