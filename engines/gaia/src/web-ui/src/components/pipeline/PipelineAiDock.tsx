/**
 * PipelineAiDock — 管道构建器右侧常驻可折叠的 AI 助手停靠面板。
 *
 * 设计参考本体对象管理页面的 AiAssistantDock（OntologyWorkspace）：
 *  - 右侧 Dock 常驻可见，与画布左右并排，对话与画布同屏不割裂。
 *  - 三态：expanded（默认 ~380px，可拖拽左边缘调宽 300~640）/
 *          collapsed（~44px 贴边图标条，点击展开）。
 *  - 宽度与折叠状态持久化到 localStorage（独立 key，与本体 Dock 偏好互不影响）。
 *
 * 与 AiAssistantDock 的区别：内容是管道专用 AssistantUiChat（pipeline_builder
 * agent + 管道 system prompt），而非本体的 AiSuggestPanel。交互外壳一致，
 * 保证全站 AI 助手体验统一。
 */
import { useCallback, useRef, useState } from 'react';
import { AssistantUiChat } from '../AssistantUiChat';
import type { HttpAgent } from '@ag-ui/client';

const STORAGE_KEY_WIDTH = 'gaia.pipeline-ai-dock.width';
const STORAGE_KEY_COLLAPSED = 'gaia.pipeline-ai-dock.collapsed';
const MIN_WIDTH = 300;
const MAX_WIDTH = 640;
const DEFAULT_WIDTH = 380;

function clamp(w: number): number {
  return Math.min(MAX_WIDTH, Math.max(MIN_WIDTH, Math.round(w)));
}

/** 读取持久化的宽度（Dock 内部自治）。 */
function loadWidth(): number {
  try {
    const raw = localStorage.getItem(STORAGE_KEY_WIDTH);
    if (raw) return clamp(JSON.parse(raw));
  } catch {
    // ignore
  }
  return DEFAULT_WIDTH;
}

/** 读取持久化的折叠态（父组件 Page 初始 state 用）。 */
export function loadPipelineDockCollapsed(): boolean {
  try {
    return Boolean(JSON.parse(localStorage.getItem(STORAGE_KEY_COLLAPSED) ?? 'false'));
  } catch {
    return false;
  }
}

function persistWidth(w: number) {
  try {
    localStorage.setItem(STORAGE_KEY_WIDTH, JSON.stringify(w));
  } catch {
    // ignore
  }
}

/** 持久化折叠态（父组件 Page 在 onCollapsedChange 调）。 */
export function persistPipelineDockCollapsed(collapsed: boolean) {
  try {
    localStorage.setItem(STORAGE_KEY_COLLAPSED, JSON.stringify(collapsed));
  } catch {
    // ignore
  }
}

interface PipelineAiDockProps {
  /** 预构建的 pipeline builder HttpAgent（usePipelineBuilderAgent 实例，
   *  内部 tap STATE_SNAPSHOT 驱动画布更新）。 */
  agent: HttpAgent;
  /** 当前管道 api_name，透传给后端做数据源 scoping。 */
  ontology: string;
  /** 系统提示词。 */
  systemPrompt: string;
  /** 初始问题（landing 页"用 AI 描述需求"带入），变化时自动发送。 */
  autoSend?: string | null;
  /** 受控折叠态（由父组件工具栏 "AI" 按钮切换）。 */
  collapsed: boolean;
  /** 折叠态变化回调（Dock 内部 ⇥ 按钮 / 拖拽无关，仅折叠切换时触发）。 */
  onCollapsedChange: (collapsed: boolean) => void;
}

export function PipelineAiDock({
  agent,
  ontology,
  systemPrompt,
  autoSend,
  collapsed,
  onCollapsedChange,
}: PipelineAiDockProps) {
  // width 仍由 Dock 自治 + localStorage 持久化（折叠态提升到父组件，
  // 以便工具栏按钮同步高亮）
  const [width, setWidth] = useState<number>(loadWidth);

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
    setWidth(w);
  }, []);
  const onPointerUp = useCallback((e: React.PointerEvent) => {
    if (!draggingRef.current) return;
    draggingRef.current = false;
    (e.target as HTMLElement).releasePointerCapture(e.pointerId);
    setWidth((w) => {
      const next = clamp(w);
      persistWidth(next);
      return next;
    });
  }, []);

  const toggle = useCallback(() => {
    onCollapsedChange(!collapsed);
  }, [collapsed, onCollapsedChange]);

  // ── 折叠态：贴边图标条 ──
  if (collapsed) {
    return (
      <aside
        className="ai-dock ai-dock--collapsed flex shrink-0 flex-col items-center gap-3 border-l border-slate-200 bg-white py-3"
        style={{ width: 44 }}
        aria-label="管道构建 AI 助手（已折叠）"
      >
        <button
          className="ai-dock-toggle"
          onClick={toggle}
          title="展开 AI 助手"
          aria-label="展开 AI 助手"
        >
          🤖
        </button>
        <span
          className="text-[11px] tracking-wide text-slate-400"
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
      className="ai-dock ai-dock--expanded relative flex shrink-0 flex-col border-l border-slate-200 bg-white"
      style={{ width }}
      aria-label="管道构建 AI 助手"
    >
      {/* Header */}
      <div className="flex items-center justify-between border-b border-slate-200 px-3 py-2.5">
        <div className="flex items-center gap-2">
          <span className="text-base">🤖</span>
          <div className="leading-tight">
            <div className="text-sm font-semibold text-slate-700">管道构建助手</div>
            <div className="text-[10px] text-slate-400">
              用自然语言描述数据管道，AI 自动在画布上构建
            </div>
          </div>
        </div>
        <button className="btn btn-xs" onClick={toggle} title="收起" aria-label="收起 AI 助手">
          ⇥
        </button>
      </div>

      {/* 对话区 */}
      <div className="flex min-h-0 flex-1 flex-col overflow-hidden">
        <AssistantUiChat
          agent={agent}
          ontology={ontology}
          systemPrompt={systemPrompt}
          autoSend={autoSend}
        />
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
