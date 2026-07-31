/**
 * AssistantUiChat — wires the AG-UI runtime to the backend /ai/agent endpoint.
 *
 * Architecture (docs/engineer/ai-integration-guide.md §3.2):
 * - `HttpAgent` (@ag-ui/client) is the SSE client. Its `headers` only accepts
 *   a static object (no request interceptor; ag-ui-protocol #1113), so
 *   dynamic JWT must be handled by rebuilding the runtime or passing the
 *   token through a RunAgentInput business field.
 * - `useAgUiRuntime` (@assistant-ui/react-ag-ui) consumes all AG-UI standard
 *   events (TEXT_MESSAGE_*, TOOL_CALL_*, STATE_*, RUN_*) automatically.
 *
 * v4.0 (ADR-009): tool-call rendering moved to Thread's default ToolCallPart
 * renderer (see assistant-ui/thread.tsx). The v3.0 `Tools({ toolkit })` +
 * `useAui({ tools })` wiring is removed — all 13 ontology tools render via
 * the default renderer without per-tool registration. The `ontologyToolkit`
 * in assistant-ui/tools.tsx is kept as an empty extension point for future
 * bespoke tool UI.
 *
 * System prompt injection (§3.3): with `manage_system_prompt='client'` (set
 * in routes/ai.py), the frontend owns the prompt. We preset it as a
 * system-role message via `thread.reset()` (ThreadRuntime public API, §7.12)
 * on mount / when the prompt changes.
 *
 * Ontology scoping (AI-context-scoping RFC): ScopedHttpAgent injects the
 * open ontology into RunAgentInput.forwardedProps.ontology.
 *
 * HITL (ADR-010 v2): write/action tools are declared with `metadata={
 * risk_level}` on the backend; pydantic-ai + AGUIAdapter turn pending tool
 * calls into AG-UI `RUN_FINISHED { outcome: { type: "interrupt" } }`. The
 * BatchApprovalPanel (mounted below the Thread, inside the runtime
 * provider) reads `unstable_getPendingInterrupts` and submits `resume` via
 * `unstable_submitInterruptResponses`. See
 * docs/bugfix/hitl-batch-approval-pending-pydantic-ai.md §4 and
 * docs/architecture/rfcs/hitl-batch-approval.md.
 */
'use client';

import { useEffect, useMemo } from 'react';
import {
  AssistantRuntimeProvider,
  useThreadRuntime,
  type ThreadMessageLike,
} from '@assistant-ui/react';
import { HttpAgent, type RunAgentInput, type RunAgentParameters } from '@ag-ui/client';
import { useAgUiRuntime } from '@assistant-ui/react-ag-ui';
import { Thread } from './assistant-ui/thread';
import { BatchApprovalPanel } from './assistant-ui/batch-approval-panel';

/** HttpAgent subclass that injects the current ontology into every
 * RunAgentInput.forwardedProps.ontology, so the backend scopes all tool
 * calls to the open ontology. Overrides the protected prepareRunAgentInput
 * hook (the runtime calls it just before sending the request). */
class ScopedHttpAgent extends HttpAgent {
  private readonly ontology: string;
  constructor(config: ConstructorParameters<typeof HttpAgent>[0], ontology: string) {
    super(config);
    this.ontology = ontology;
  }
  protected override prepareRunAgentInput(parameters?: RunAgentParameters): RunAgentInput {
    const input = super.prepareRunAgentInput(parameters);
    return {
      ...input,
      forwardedProps: { ...(input.forwardedProps ?? {}), ontology: this.ontology },
    };
  }
}

interface AssistantUiChatProps {
  /** System prompt preset as the thread's first message. */
  systemPrompt?: string;
  /** The ontology api_name the user currently has open. Forwarded to the
   *  backend via RunAgentInput.forwardedProps.ontology so the agent scopes
   *  all tool calls to this ontology (the AG-UI path does not register
   *  list_ontologies). Empty/undefined = no scoping. */
  ontology?: string;
  /** Optional pre-built HttpAgent. When provided (e.g. the graph-explore
   *  page's GraphExploreAgent that taps STATE_SNAPSHOT events to drive the
   *  canvas), it is used instead of constructing a ScopedHttpAgent here.
   *  The caller is responsible for ontology scoping in that case. */
  agent?: HttpAgent;
  /** Optional initial question to auto-send on mount / when it changes.
   *  ADR-015: the graph-explore landing dialog hands the question off to the
   *  exploring mode's AG-UI Thread by setting this; the Thread auto-sends it
   *  so the user does not have to re-type. Use a stable key (e.g. timestamp)
   *  to force re-send of an identical question. */
  autoSend?: string | null;
  /** Optional children rendered inside the runtime provider (can use runtime hooks). */
  children?: React.ReactNode;
}

/** Presets the system message on the thread whenever it changes. */
function SystemPromptPreset({ systemPrompt }: { systemPrompt?: string }) {
  const thread = useThreadRuntime();
  useEffect(() => {
    if (!systemPrompt) return;
    const initial: ThreadMessageLike[] = [{ role: 'system', content: systemPrompt }];
    try {
      thread.reset(initial);
    } catch (e) {
      // reset may throw if called before the runtime is fully ready; ignore.
      console.warn('[AssistantUiChat] thread.reset failed:', e);
    }
  }, [thread, systemPrompt]);
  return null;
}

/** Auto-sends a message on mount / when `message` changes.
 *  Lives inside the runtime provider so it can call useThreadRuntime().append().
 *  ADR-015: graph-explore landing → exploring handoff uses this to send the
 *  user's question without making them re-type it in the Thread composer. */
function AutoSendMessage({ message }: { message: string }) {
  const thread = useThreadRuntime();
  useEffect(() => {
    if (!message) return;
    // Append as a user message. assistant-ui's append triggers a run when the
    // thread is in a sendable state. We guard with a small delay to let the
    // SystemPromptPreset reset land first (reset + append in the same tick
    // can race on first mount).
    const id = window.setTimeout(() => {
      try {
        thread.append({ role: 'user', content: [{ type: 'text', text: message }] });
      } catch (e) {
        console.warn('[AssistantUiChat] auto-send failed:', e);
      }
    }, 0);
    return () => window.clearTimeout(id);
  }, [thread, message]);
  return null;
}

export function AssistantUiChat({ systemPrompt, ontology, agent: externalAgent, autoSend, children }: AssistantUiChatProps) {
  // HttpAgent is constructed once per `ontology` change. ScopedHttpAgent
  // injects the current ontology into RunAgentInput.forwardedProps.ontology
  // so the backend's AppState.ontology scopes every tool call to the open
  // ontology. Rebuilding on ontology change also resets the thread (a context
  // switch starts a fresh conversation). For dynamic JWT, rebuild the runtime
  // similarly — see guide §2.5.
  // ADR-015: callers that need to tap the event stream (e.g. graph-explore's
  // canvas driver) pass their own agent via the `agent` prop.
  const internalAgent = useMemo(
    () =>
      new ScopedHttpAgent(
        {
          url: '/ai/agent',
          headers: {
            Accept: 'text/event-stream',
          },
        },
        ontology ?? '',
      ),
    [ontology],
  );
  const agent = externalAgent ?? internalAgent;

  const runtime = useAgUiRuntime({
    agent,
    showThinking: true,
    onError: (e) => console.error('[ag-ui]', e),
  });

  return (
    <AssistantRuntimeProvider runtime={runtime}>
      <SystemPromptPreset systemPrompt={systemPrompt} />
      {autoSend && <AutoSendMessage message={autoSend} />}
      <div className="flex h-full min-h-0 flex-col">
        <Thread />
        <BatchApprovalPanel />
      </div>
      {children}
    </AssistantRuntimeProvider>
  );
}
