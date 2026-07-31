/**
 * BatchApprovalPanel — AG-UI native interrupt/resume HITL (ADR-010 v2).
 *
 * When the agent's run ends with pending `requires_approval`-style tool
 * calls, pydantic-ai + AGUIAdapter emit
 * `RUN_FINISHED { outcome: { type: "interrupt", interrupts: [...] } }`.
 * `useAgUiRuntime` surfaces these as `unstable_getPendingInterrupts()`.
 *
 * Protocol constraint: `submitInterruptResponses` REQUIRES a response for
 * EVERY open interrupt in one shot (the AG-UI resume array must cover all
 * open interrupts — partial resume is rejected with "missing responses for
 * open interrupts"). So this panel collects a per-item decision (approve /
 * deny / undecided) and submits them ALL at once via a single "提交" button.
 *
 * UX:
 *   - Each row: tool name + args + risk badge + 批准/拒绝 toggle (sets the
 *     per-item decision without submitting).
 *   - Footer: "全部批准" / "全部拒绝" set every item's decision at once;
 *     "提交 N 项审批" submits all decisions (enabled once every item has a
 *     decision). high/unknown risk disables "全部批准" (must review
 *     per-item) but per-item 批准 still works.
 *   - The interrupt list scrolls (max-height) so 12+ items don't push the
 *     footer off-screen.
 *
 * See docs/bugfix/hitl-batch-approval-pending-pydantic-ai.md §4 and
 * docs/architecture/rfcs/hitl-batch-approval.md §3.
 */
'use client';

import { useState } from 'react';
import { useAssistantRuntime, useThread } from '@assistant-ui/react';
import type { AgUiAssistantRuntime } from '@assistant-ui/react-ag-ui';
import { cn } from '../../lib/cn';
import {
  interruptRiskLevel,
  type InterruptRiskLevel,
  type PendingInterrupt,
} from '../../api/types';

type Decision = 'approve' | 'deny';

/** Risk-level badge for a pending interrupt. */
function RiskBadge({ risk }: { risk: InterruptRiskLevel }) {
  const label = risk === 'medium' ? '中风险' : risk === 'high' ? '高风险' : '风险未知';
  const cls =
    risk === 'high'
      ? 'bg-error text-white'
      : risk === 'medium'
        ? 'bg-[var(--accent-bg)] text-accent-text'
        : 'bg-border text-text-secondary';
  return <span className={cn('rounded-pill px-2 py-0.5 text-[11px]', cls)}>{label}</span>;
}

/** Parse the tool name + args out of the interrupt message for display.
 * pydantic-ai's approval_to_interrupt builds `message` as
 * `Approve <tool_name>(<args_json>)?`.
 *
 * P0 optimisation (2026-07-08): when the backend attaches an
 * `impact_summary` + `resolved_args` to `interrupt.metadata` (via
 * MetadataApprovalToolset + impact_builder), the panel renders the
 * plain-language summary instead of the raw JSON — per the HITL best
 * practice "show the effect of the action, not the JSON". The tool name
 * is still parsed from `message` (the summary doesn't carry it), but the
 * args preview comes from `metadata.impact_summary` when available. */
function parseInterruptDisplay(interrupt: PendingInterrupt): {
  toolName: string;
  argsPreview: string;
  /** Whether argsPreview is a human-readable impact summary (true) or raw
   *  JSON args (false). Drives the rendering style: summary is rendered as
   *  plain text; raw args as a <pre> code block. */
  isImpactSummary: boolean;
} {
  const msg = interrupt.message ?? '';
  const m = msg.match(/^Approve\s+([^(]+)\((.*)\)\??$/s);
  const toolName = m ? m[1].trim() : (interrupt.toolCallId ?? interrupt.id);
  // Prefer the backend's human-readable impact summary + resolved args
  // (ontology defaults applied) over the raw LLM args. Falls back to the
  // raw args from the message when no summary is attached.
  const meta = interrupt.metadata as Record<string, unknown> | undefined;
  const impactSummary = meta?.['impact_summary'];
  if (typeof impactSummary === 'string' && impactSummary) {
    return { toolName, argsPreview: impactSummary, isImpactSummary: true };
  }
  const rawArgs = m ? m[2] : msg;
  return { toolName, argsPreview: rawArgs, isImpactSummary: false };
}

export function BatchApprovalPanel() {
  // useThread subscribes to thread state so the panel re-renders when a run
  // finishes (interrupt arrives) or resumes (interrupts clear).
  useThread();
  const runtime = useAssistantRuntime() as AgUiAssistantRuntime;
  const pending: readonly PendingInterrupt[] = runtime.unstable_getPendingInterrupts
    ? runtime.unstable_getPendingInterrupts()
    : [];

  // Per-interrupt decision, keyed by interrupt id. Cleared whenever the set
  // of pending interrupts changes (new run / resume).
  //
  // Reset-on-pending-change is done during render (the React-recommended
  // "adjusting state during render" pattern) rather than in an effect, to
  // avoid the cascading renders flagged by react-hooks/set-state-in-effect.
  const pendingKey = pending.map((i) => i.id).join('|');
  const [decisions, setDecisions] = useState<Record<string, Decision>>({});
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [lastPendingKey, setLastPendingKey] = useState(pendingKey);
  if (pendingKey !== lastPendingKey) {
    setLastPendingKey(pendingKey);
    setDecisions({});
    setError(null);
  }

  if (pending.length === 0) return null;

  const risks = pending.map(interruptRiskLevel);
  const hasHighOrUnknown = risks.some((r) => r === 'high' || r === 'unknown');
  const decidedCount = pending.filter((i) => decisions[i.id]).length;
  const allDecided = decidedCount === pending.length;

  const setDecision = (id: string, d: Decision) => setDecisions((prev) => ({ ...prev, [id]: d }));

  // Set every item to the same decision AND submit immediately. "全部批准/拒绝"
  // is a one-shot action — no point forcing a separate "提交" click afterwards.
  // (Satisfies the AG-UI constraint that a resume must cover all open
  // interrupts in one shot.)
  const submitAll = async (d: Decision) => {
    if (busy) return;
    const next: Record<string, Decision> = {};
    for (const i of pending) next[i.id] = d;
    setDecisions(next);
    setBusy(true);
    setError(null);
    try {
      await runtime.unstable_submitInterruptResponses(
        pending.map((i) => ({
          interruptId: i.id,
          status: 'resolved' as const,
          payload: { approved: d === 'approve' },
        })),
      );
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  };

  const submit = async () => {
    if (busy || !allDecided) return;
    setBusy(true);
    setError(null);
    try {
      await runtime.unstable_submitInterruptResponses(
        pending.map((i) => ({
          interruptId: i.id,
          status: 'resolved' as const,
          payload: { approved: decisions[i.id] === 'approve' },
        })),
      );
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="my-2 flex max-h-[420px] flex-col rounded-md border border-[var(--accent-bg)] bg-[var(--accent-bg)] p-2.5 text-[12px]">
      <div className="mb-2 flex flex-shrink-0 items-center gap-2 font-semibold text-accent-text">
        <span>🔍 批量审批</span>
        <span className="text-text-muted">
          ({decidedCount}/{pending.length} 已决定)
        </span>
      </div>

      {/* Scrollable interrupt list — keeps the footer visible for 12+ items. */}
      <div className="flex flex-1 flex-col gap-1.5 overflow-y-auto pr-0.5">
        {pending.map((interrupt, idx) => {
          const risk = risks[idx];
          const { toolName, argsPreview, isImpactSummary } = parseInterruptDisplay(interrupt);
          const dec = decisions[interrupt.id];
          return (
            <div key={interrupt.id} className="rounded-md border border-border bg-surface p-2">
              <div className="flex items-center gap-2">
                <span className="font-mono font-semibold text-accent-text">{toolName}</span>
                <RiskBadge risk={risk} />
              </div>
              {argsPreview &&
                (isImpactSummary ? (
                  // Human-readable impact summary (P0): plain text, not JSON.
                  // Renders the real effect (ontology defaults applied, plain
                  // language) so the user can predict the outcome.
                  <p className="mt-1 rounded bg-[var(--accent-bg)] p-1.5 text-[11px] leading-relaxed text-text-secondary">
                    {argsPreview}
                  </p>
                ) : (
                  // Fallback: raw JSON args from the interrupt message.
                  <pre className="mt-1 overflow-x-auto rounded bg-[var(--accent-bg)] p-1.5 text-[11px] text-text-secondary">
                    {argsPreview.length > 400 ? argsPreview.slice(0, 400) + '…' : argsPreview}
                  </pre>
                ))}
              <div className="mt-1.5 flex gap-2">
                <button
                  className={cn('btn btn-sm', dec === 'approve' && 'btn-primary')}
                  onClick={() => setDecision(interrupt.id, 'approve')}
                >
                  ✅ 批准
                </button>
                <button
                  className={cn('btn btn-sm', dec === 'deny' && 'btn-primary')}
                  onClick={() => setDecision(interrupt.id, 'deny')}
                >
                  ⏭ 拒绝
                </button>
              </div>
            </div>
          );
        })}
      </div>

      {error && <div className="mt-2 flex-shrink-0 text-error">⚠️ {error}</div>}

      {/* Footer — always visible (list scrolls above it). */}
      <div className="mt-2 flex flex-shrink-0 flex-wrap items-center gap-2 border-t border-border pt-2">
        <button
          className="btn btn-primary btn-sm"
          disabled={busy || hasHighOrUnknown}
          title={hasHighOrUnknown ? '含高风险或风险未知的操作，须逐个确认' : '全部批准并提交'}
          onClick={() => submitAll('approve')}
        >
          {busy ? '…提交中' : '✅ 全部批准'}
        </button>
        <button className="btn btn-sm" disabled={busy} onClick={() => submitAll('deny')}>
          ⏭ 全部拒绝
        </button>
        {/* Per-item review path: set decisions individually, then submit all
            at once. Only shown when blanket-approve isn't available (high /
            unknown risk) OR the user has started picking per-item decisions. */}
        <button
          className="btn btn-sm"
          disabled={busy || !allDecided}
          title={!allDecided ? '请先对每一项作出决定' : '提交逐项决定，agent 续跑'}
          onClick={submit}
        >
          {busy ? '…' : `提交 ${pending.length} 项`}
        </button>
        {hasHighOrUnknown && (
          <span className="text-[11px] text-text-muted">含高风险/未知操作，须逐个确认</span>
        )}
      </div>
    </div>
  );
}
