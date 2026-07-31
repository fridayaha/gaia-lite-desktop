/**
 * AiSuggestPanel — AG-UI powered ontology assistant panel.
 *
 * v4.0 (ADR-009): the panel is now a READ-ONLY conversational assistant.
 * The v3.0 "建议 → 查重 → 批量创建" flow (ApplyBar parsing JSON suggestions
 * from the deleted `apply_suggestions` tool and calling onApply) is removed —
 * the backend demo tools are gone, replaced by 13 read-only ontology tools
 * (list_ontologies / get_object / filter_object / aggregate_object / ...).
 * The Agent now answers queries by calling those tools; it cannot create
 * objects in MVP (write/action tools + HITL land in Sprint 2).
 *
 * The assistant-ui Thread handles streaming render + tool-call display
 * natively (see assistant-ui/thread.tsx). This component is just a styled
 * wrapper around AssistantUiChat with a system prompt.
 */
'use client';

import { AssistantUiChat } from './AssistantUiChat';
import { buildOntologyQueryPrompt } from '../api/prompts';

interface AiSuggestPanelProps {
  /** The ontology api_name the user currently has open. Scopes the assistant
   *  to this ontology (forwarded to the backend + injected into the prompt). */
  ontology: string;
  /** 裸模式：去掉外层卡片包装与标题，由父容器（AiAssistantDock）提供外壳。
   *  默认 false 保留独立卡片形态，便于其它上下文复用。 */
  bare?: boolean;
}

export function AiSuggestPanel({ ontology, bare = false }: AiSuggestPanelProps) {
  if (bare) {
    // Dock 内：撑满高度，对话区可滚动
    return (
      <div className="flex h-full flex-col">
        <AssistantUiChat ontology={ontology} systemPrompt={buildOntologyQueryPrompt(ontology)} />
      </div>
    );
  }
  return (
    <div className="mb-5 rounded-md border border-border bg-surface p-3">
      <div className="mb-2 flex items-center gap-2">
        <span className="text-base">🤖</span>
        <span className="text-sm font-semibold">BuildWith 本体建模助手</span>
        <span className="ml-1 text-[11px] text-text-muted">
          用业务语言构建本体（对象、关系、动作等），支持自然语言查询与批量生成
        </span>
      </div>
      <AssistantUiChat ontology={ontology} systemPrompt={buildOntologyQueryPrompt(ontology)} />
    </div>
  );
}
