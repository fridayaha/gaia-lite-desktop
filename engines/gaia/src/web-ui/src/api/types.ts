/**
 * AI / AG-UI shared types (ADR-009 + ADR-010 v2: native interrupt/resume).
 *
 * Field names are snake_case to match the backend pydantic serialization
 * (pydantic emits snake_case by default). Writing camelCase here would
 * silently fail to bind — see docs/engineer/ai-integration-guide.md §3.4.
 *
 * HITL (ADR-010 v2): write/action tools are declared with
 * `metadata={risk_level}` on the backend; pydantic-ai + AGUIAdapter turn a
 * pending tool call into an AG-UI `RUN_FINISHED { outcome: { type:
 * "interrupt" } }` carrying one `Interrupt` per pending call. The frontend
 * renders a batch-approval panel (BatchApprovalPanel) and submits `resume`
 * via `useAgUiRuntime().unstable_submitInterruptResponses`. No
 * NEED_APPROVAL marker, no /ai/action/confirm endpoint.
 */

/**
 * The risk_level carried on `Interrupt.metadata.risk_level` for a pending
 * write/action tool call.
 *
 * - "medium": modelling writes (define_object_type, add_property, ...).
 *   Batch-approvable.
 * - "unknown": invoke_action (risk_level only known at runtime from the
 *   ActionType definition). The batch panel defaults these to per-item
 *   review (no blanket-approve) — conservative.
 * - "high": reserved for future high-risk static declarations. The batch
 *   panel disables blanket-approve.
 */
export type InterruptRiskLevel = 'medium' | 'high' | 'unknown';

/** Subset of AG-UI Interrupt the batch panel reads. Mirrors
 * `AgUiInterrupt` from @assistant-ui/react-ag-ui (re-declared here so the
 * panel doesn't depend on that package's internal type path). */
export interface PendingInterrupt {
  id: string;
  reason: string;
  message?: string;
  toolCallId?: string;
  metadata?: Record<string, unknown>;
}

/** Read the risk_level off an interrupt's metadata (defensive). */
export function interruptRiskLevel(interrupt: PendingInterrupt): InterruptRiskLevel {
  const v = interrupt.metadata?.['risk_level'];
  return v === 'medium' || v === 'high' || v === 'unknown' ? v : 'unknown';
}

/** The user's per-interrupt decision for the batch panel. */
export interface InterruptDecision {
  approved: boolean;
}

/**
 * The ObjectType suggestion payload. Kept for Sprint 2 (write/action tools +
 * HITL) which will restore an "AI suggests → human approves → create" flow.
 */
export type { AiObjectTypeSuggestion, AiPropertySuggestion, AiLinkSuggestion } from '../types';
