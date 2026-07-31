/**
 * AiAssistantDock — 右侧常驻可折叠的 AI 本体助手停靠面板。
 *
 * 设计动机（见 OntologyWorkspace 设计评审）：
 *  - 原先 AI 助手在主区顶部与对象列表"上下"分屏，压缩了主任务区纵向空间。
 *  - 改为右侧 Dock：对象列表/图谱拿回完整主区；助手常驻可见（AI 原生辅助，
 *    不藏进悬浮图标以免降低发现性），且与主区"左右并排"——对话与数据同屏，
 *    上下文不割裂。
 *
 * 三态交互：
 *  - expanded（默认）：中等宽度（~380px），可拖拽右边缘调宽（300~640）。
 *  - collapsed：折叠为 ~44px 贴边图标条，点击展开。等价"悬浮图标"形态，
 *    但贴边不遮挡内容，且保留一键召回。
 *  - 与 ObjectDetailPanel 互斥：父组件传入 detailOpen，详情面板打开时本 Dock
 *    自动折叠（同一时刻仅一个右侧面板全开，避免三栏过窄）；详情关闭可手动展开。
 *
 * 宽度与折叠状态持久化到 localStorage，跨会话记忆用户偏好。
 */
import { useCallback, useEffect, useRef, useState } from 'react';
import { AiSuggestPanel } from './AiSuggestPanel';

const STORAGE_KEY = 'gaia.ai-dock';
const MIN_WIDTH = 300;
const MAX_WIDTH = 640;
const DEFAULT_WIDTH = 340;

interface Persisted {
  width: number;
  collapsed: boolean;
}

function load(): Persisted {
  try {
    const raw = localStorage.getItem(STORAGE_KEY);
    if (raw) {
      const p = JSON.parse(raw) as Partial<Persisted>;
      return {
        width: clamp(p.width ?? DEFAULT_WIDTH),
        collapsed: Boolean(p.collapsed),
      };
    }
  } catch {
    // ignore
  }
  return { width: DEFAULT_WIDTH, collapsed: false };
}

function clamp(w: number): number {
  return Math.min(MAX_WIDTH, Math.max(MIN_WIDTH, Math.round(w)));
}

function persist(p: Persisted) {
  try {
    localStorage.setItem(STORAGE_KEY, JSON.stringify(p));
  } catch {
    // ignore
  }
}

interface AiAssistantDockProps {
  ontology: string;
  /** ObjectDetailPanel 是否打开。打开时本 Dock 折叠（互斥）。 */
  detailOpen: boolean;
  /** 详情面板打开时，用户点击折叠态图标要求展开助手的回调。
   *  父组件应在此关闭详情面板（保持“同一时刻仅一个右侧面板全开”），
   *  随后 detailOpen 变 false，本 Dock 自动展开。 */
  onForceExpand?: () => void;
  /** 实际折叠状态变化回调。父级（OntologyWorkspace）用它联动左侧 sidebar：
   *  dock 展开（collapsed→false）时自动收起 sidebar。 */
  onCollapsedChange?: (collapsed: boolean) => void;
}

export function AiAssistantDock({
  ontology,
  detailOpen,
  onForceExpand,
  onCollapsedChange,
}: AiAssistantDockProps) {
  // lazy initializer：仅首次渲染读一次 localStorage（两个 state 共享同一次读取）
  const [state, setState] = useState<Persisted>(load);
  const width = state.width;
  const userCollapsed = state.collapsed;
  const collapsed = detailOpen || userCollapsed;

  // 拖拽调宽
  const draggingRef = useRef(false);
  const onPointerDown = useCallback((e: React.PointerEvent) => {
    e.preventDefault();
    draggingRef.current = true;
    (e.target as HTMLElement).setPointerCapture(e.pointerId);
  }, []);
  const onPointerMove = useCallback((e: React.PointerEvent) => {
    if (!draggingRef.current) return;
    // 右侧 Dock：宽度 = 视口右边缘 - 指针 x
    const w = clamp(window.innerWidth - e.clientX);
    setState((s) => ({ ...s, width: w }));
  }, []);
  const onPointerUp = useCallback((e: React.PointerEvent) => {
    if (!draggingRef.current) return;
    draggingRef.current = false;
    (e.target as HTMLElement).releasePointerCapture(e.pointerId);
    setState((s) => {
      const next = { ...s, width: clamp(s.width) };
      persist(next);
      return next;
    });
  }, []);

  const toggle = useCallback(() => {
    setState((s) => {
      const next = { ...s, collapsed: !s.collapsed };
      persist(next);
      return next;
    });
  }, []);

  // 通知父级实际折叠状态变化（联动左侧 sidebar：dock 展开时收起 sidebar）。
  useEffect(() => {
    onCollapsedChange?.(collapsed);
  }, [collapsed, onCollapsedChange]);

  // ── 折叠态：贴边图标条 ──
  if (collapsed) {
    return (
      <aside
        className="ai-dock ai-dock--collapsed flex shrink-0 flex-col items-center gap-3 border-l border-border bg-sidebar py-3"
        style={{ width: 44 }}
        aria-label="BuildWith 本体建模助手（已折叠）"
      >
        <button
          className="ai-dock-toggle"
          onClick={() => {
            if (detailOpen) {
              // 详情面板打开中：先要求父组件关闭详情，Dock 随 detailOpen 变 false 自动展开
              onForceExpand?.();
            } else {
              toggle();
            }
          }}
          title={detailOpen ? '展开 AI 助手（将关闭对象详情）' : '展开 AI 助手'}
          aria-label="展开 AI 助手"
        >
          🤖
        </button>
        <span
          className="text-[11px] tracking-wide text-text-muted"
          style={{ writingMode: 'vertical-rl' }}
        >
          AI 助手
        </span>
      </aside>
    );
  }

  // ── 展开态 ──
  return (
    <aside
      className="ai-dock ai-dock--expanded relative flex shrink-0 flex-col border-l border-border bg-sidebar"
      style={{ width }}
      aria-label="BuildWith 本体建模助手"
    >
      {/* Header */}
      <div className="flex items-center justify-between border-b border-border px-3 py-2.5">
        <div className="flex items-center gap-2">
          <span className="text-base">🤖</span>
          <div className="leading-tight">
            <div className="text-sm font-semibold text-text">BuildWith 本体建模助手</div>
            <div className="text-[10px] text-text-muted">
              用业务语言构建本体（对象、关系、动作等），支持自然语言查询与批量生成
            </div>
          </div>
        </div>
        <button className="btn btn-xs" onClick={toggle} title="收起" aria-label="收起 AI 助手">
          ⇥
        </button>
      </div>

      {/* 对话区（AiSuggestPanel 去掉外层卡片，由 Dock 提供容器） */}
      <div className="flex min-h-0 flex-1 flex-col overflow-hidden">
        <AiSuggestPanel ontology={ontology} bare />
      </div>

      {/* 拖拽调宽手柄 */}
      <div
        className="ai-dock-resizer"
        onPointerDown={onPointerDown}
        onPointerMove={onPointerMove}
        onPointerUp={onPointerUp}
        onPointerCancel={onPointerUp}
        role="separator"
        aria-orientation="vertical"
        aria-label="拖拽调整 AI 助手宽度"
      />
    </aside>
  );
}
