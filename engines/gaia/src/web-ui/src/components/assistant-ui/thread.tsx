/**
 * assistant-ui Thread component (local source, customized for Gaia design).
 *
 * The reference guide (docs/engineer/ai-integration-guide.md §3.1) installs
 * this from the shadcn registry, but this project has no shadcn setup, so we
 * compose it directly from @assistant-ui/react primitives using the project's
 * semantic CSS classes (btn / form-input / card).
 *
 * Tool-call rendering: per assistant-ui 0.14, `MessagePrimitive.Parts` takes
 * a children render function receiving `{ part }`. For `tool-call` parts we
 * render a compact default card (tool name + status + args + result). This
 * covers all 13 ontology tools uniformly without per-tool renderer
 * registration — bespoke tool UI can be added later by branching on
 * `part.toolName`. The old v3.0 per-tool renderers (suggest_object_types /
 * apply_suggestions) were removed in v4.0 when those demo tools were deleted
 * from the backend (ADR-009).
 *
 * HITL (ADR-010 v2): write/action tool approvals are NO LONGER rendered
 * inline as NEED_APPROVAL markers. Pending tool calls become AG-UI
 * interrupts; the BatchApprovalPanel (mounted alongside the Thread in
 * AssistantUiChat) renders them as a batch and submits `resume`. The
 * tool-call card here only renders completed/running tool calls.
 */
import {
  ComposerPrimitive,
  MessagePrimitive,
  ThreadPrimitive,
  useMessage,
} from '@assistant-ui/react';
import TextareaAutosize from 'react-textarea-autosize';
import { useState, useRef } from 'react';
import { Disclosure } from '../ui/Disclosure';
import { cn } from '../../lib/cn';
import { MarkdownText } from './markdown-text';

/** Compact JSON preview, truncated for long values to keep the chat scannable. */
function JsonPreview({ value, maxChars = 400 }: { value: unknown; maxChars?: number }) {
  let text: string;
  try {
    text = typeof value === 'string' ? value : JSON.stringify(value, null, 2);
  } catch {
    text = String(value);
  }
  if (text.length > maxChars) {
    text = text.slice(0, maxChars) + '…';
  }
  return (
    <pre className="mt-1 overflow-x-auto rounded bg-[var(--accent-bg)] p-1.5 text-[11px] text-text-secondary">
      {text}
    </pre>
  );
}

/** Render a tool-call part: name + status badge + args + result. */
function ToolCallPart({
  part,
}: {
  part: {
    type: 'tool-call';
    toolName: string;
    argsText: string;
    args?: unknown;
    result?: unknown;
    status?: { type: string };
    isError?: boolean;
  };
}) {
  // status.type: "running" | "complete" | "incomplete" | "requires-action" (assistant-ui)
  const statusType = part.status?.type ?? 'complete';
  const running = statusType === 'running';
  const errored = part.isError || statusType === 'incomplete';

  const badge = running ? '⏳ 运行中' : errored ? '⚠️ 未完成' : '✅ 完成';
  const badgeClass = running ? 'text-text-muted' : errored ? 'text-error' : 'text-[var(--success)]';

  // Args: prefer parsed `args`, fall back to raw argsText.
  const args = part.args ?? (part.argsText && part.argsText !== '{}' ? part.argsText : null);
  const hasDetails = args != null || (!running && part.result != null);

  // 受控展开状态：运行中默认展开（看进度），完成/出错默认折叠（省空间）。
  // 用户手动点击后尊重其选择，不再因 status 变化自动收起/展开。
  const [expanded, setExpanded] = useState(running);
  const userToggled = useRef(false);
  const prevRunning = useRef(running);
  if (prevRunning.current && !running && !userToggled.current) {
    // running→complete 且用户未手动操作过：自动收起
    if (expanded) setExpanded(false);
  }
  prevRunning.current = running;

  // 无详情（无参数无结果）时只显示一行徽章，不裹 Disclosure。
  if (!hasDetails) {
    return (
      <div className="my-1.5 flex items-center gap-2 rounded-md border border-border bg-surface px-2 py-1.5 text-[12px]">
        <span className="font-mono font-semibold text-accent-text">{part.toolName}</span>
        <span className={badgeClass}>{badge}</span>
      </div>
    );
  }

  return (
    <div className="my-1.5 rounded-md border border-border bg-surface text-[12px]">
      <Disclosure
        isExpanded={expanded}
        onExpandedChange={(next) => {
          userToggled.current = true;
          setExpanded(next);
        }}
        triggerClassName="px-2 py-1.5 text-[12px]"
        panelClassName="px-2 py-1.5"
        trigger={
          <span className="flex items-center gap-2">
            <span className="font-mono font-semibold text-accent-text">{part.toolName}</span>
            <span className={badgeClass}>{badge}</span>
          </span>
        }
      >
        {args != null && (
          <div className="mt-0.5">
            <span className="text-text-muted">参数:</span>
            <JsonPreview value={args} />
          </div>
        )}
        {!running && part.result != null && (
          <div className="mt-1">
            <span className="text-text-muted">结果:</span>
            <JsonPreview value={part.result} />
          </div>
        )}
      </Disclosure>
    </div>
  );
}

/** A single message row. Renders system messages as nothing (the system
 * prompt is context, not conversation — showing it pollutes the thread) and
 * distinguishes user vs assistant per chat-UI best practice: user messages
 * are right-aligned bubbles, assistant messages are left-aligned with an
 * avatar. */
function MessageRow() {
  const role = useMessage((state) => state.role);

  // System prompt is preset context — never render it as a visible message.
  if (role === 'system') return null;

  const isUser = role === 'user';
  return (
    <MessagePrimitive.Root
      className={cn('mb-3 flex flex-col gap-1', isUser ? 'items-end' : 'items-start')}
    >
      <div className={cn('flex items-start gap-2 w-full', isUser && 'flex-row-reverse')}>
        <span className={cn('mt-0.5 select-none text-base', isUser ? 'opacity-0' : '')}>
          {isUser ? '🧑' : '🤖'}
        </span>
        <div
          className={cn(
            'min-w-0 max-w-[85%] rounded-lg px-3 py-2 text-[13px] leading-relaxed',
            isUser
              ? 'bg-[var(--accent-bg)] text-accent-text'
              : 'bg-surface text-text border border-border',
          )}
        >
          <MessagePrimitive.Parts>
            {({ part }) => {
              switch (part.type) {
                case 'text':
                  return <MarkdownText />;
                case 'tool-call':
                  return <ToolCallPart part={part} />;
                case 'reasoning':
                  return (
                    <details className="text-[12px] text-text-muted">
                      <summary>思考过程</summary>
                      <p className="whitespace-pre-wrap">{part.text}</p>
                    </details>
                  );
                default:
                  // image / source / file / etc. — not used by ontology tools.
                  return null;
              }
            }}
          </MessagePrimitive.Parts>
        </div>
      </div>
    </MessagePrimitive.Root>
  );
}

/** Composer (input + send/cancel). */
function Composer() {
  return (
    <ComposerPrimitive.Root className="flex items-end gap-2">
      <ComposerPrimitive.Input asChild>
        <TextareaAutosize
          className="form-input min-h-[40px] flex-1 resize-none"
          minRows={1}
          maxRows={6}
          placeholder=""
          onKeyDown={(e: React.KeyboardEvent<HTMLTextAreaElement>) => {
            if (e.key === 'Enter' && (e.metaKey || e.ctrlKey)) {
              e.preventDefault();
              e.currentTarget.form?.requestSubmit();
            }
          }}
        />
      </ComposerPrimitive.Input>
      <ComposerPrimitive.Send className="btn btn-primary btn-sm">发送</ComposerPrimitive.Send>
      <ComposerPrimitive.Cancel className="btn btn-sm">取消</ComposerPrimitive.Cancel>
    </ComposerPrimitive.Root>
  );
}

/** Full Thread: scrollable message list + composer. */
export function Thread() {
  return (
    // 撑满父容器高度（父级为 flex 列布局），长对话在内部滚动而非撑高面板。
    <ThreadPrimitive.Root className="flex h-full min-h-0 flex-col">
      <ThreadPrimitive.Viewport className="min-h-0 flex-1 overflow-y-auto px-1 pb-3">
        <ThreadPrimitive.Empty>
          <div className="flex h-full flex-col items-center justify-center gap-1 p-6 text-center text-sm text-text-muted">
            <span className="text-2xl">🤖</span>
            <span>用业务语言描述，构建或查询当前本体</span>
            <span className="text-[11px]">对象类型 · 关系 · 动作 · 聚合统计</span>
          </div>
        </ThreadPrimitive.Empty>
        <ThreadPrimitive.Messages>{() => <MessageRow />}</ThreadPrimitive.Messages>
      </ThreadPrimitive.Viewport>
      <div className="border-t border-border p-3">
        <Composer />
      </div>
    </ThreadPrimitive.Root>
  );
}
