/**
 * useGraphExploreAgent — AG-UI Agent 驱动图探索画布（ADR-015 路线 1）。
 *
 * 替代旧的 useConversation + usePlanExecutor + queryNL/explorePlan 三套机制。
 * Agent（/ai/agent ReAct 循环）通过工具调用驱动画布：
 *   - query_with_dataframe / traverse_link（后端数据工具）→ 返回 STATE_SNAPSHOT，
 *     state.canvas.objects 由本 hook 订阅后调 explore.loadStartSet 灌画布。
 *   - switch_view / color_by（后端 UI 工具）→ 返回 STATE_SNAPSHOT，
 *     state.canvas.view / color_by 由本 hook 订阅后调 setView / setLayerStyle。
 *
 * 机制：自定义 HttpAgent 子类拦截 SSE 事件流里的 STATE_SNAPSHOT，解析
 * state.canvas 调对应的 useGraphExplore API。Agent 每轮读 state 决策
 * （ReAct observe）——0 对象时自然终止（ADR-015 D5）。
 */
import { useMemo, useRef } from 'react';
import { HttpAgent, type RunAgentInput, type BaseEvent, EventType } from '@ag-ui/client';
import { tap } from 'rxjs';
import type { GraphExploreState, CanvasSnapshot } from '../types/canvas';
import type { useGraphExplore } from './useGraphExplore';

/** HttpAgent subclass that:
 *  1. Injects the current ontology into RunAgentInput.forwardedProps.ontology
 *     (scopes all tool calls to the open ontology).
 *  2. Taps the SSE event stream to intercept STATE_SNAPSHOT events and drive
 *     the canvas (loadStartSet / setView / setLayerStyle) via onCanvasState. */
class GraphExploreAgent extends HttpAgent {
  private readonly ontology: string;
  private readonly onCanvasState: (canvas: CanvasSnapshot) => void;
  /** 上一次处理的 STATE_SNAPSHOT 指纹。assistant-ui runtime 在 re-render 时
   * 会重放整个事件流，导致同一 snapshot 被多次处理 → setState → re-render →
   * 重放 → 死循环。在 SSE 层面指纹去重，重放的相同 snapshot 不触发回调。 */
  private lastSnapshotFingerprint = '';

  constructor(
    config: ConstructorParameters<typeof HttpAgent>[0],
    ontology: string,
    onCanvasState: (canvas: CanvasSnapshot) => void,
  ) {
    super(config);
    this.ontology = ontology;
    this.onCanvasState = onCanvasState;
  }

  protected override prepareRunAgentInput(parameters?: Parameters<HttpAgent['runAgent']>[0]): RunAgentInput {
    const input = super.prepareRunAgentInput(parameters);
    return {
      ...input,
      forwardedProps: { ...(input.forwardedProps ?? {}), ontology: this.ontology },
    };
  }

  override run(input: RunAgentInput) {
    const stream = super.run(input);
    // Tap the event stream: intercept STATE_SNAPSHOT to drive the canvas.
    // We return the original stream unchanged (assistant-ui runtime consumes
    // it for message/tool rendering); the side effect is canvas updates.
    //
    // 指纹去重：runtime re-render 时重放事件流，同一 STATE_SNAPSHOT 只处理首次，
    // 避免下游 setState → re-render → 重放 → 死循环。
    return stream.pipe(
      tap((event: BaseEvent) => {
        if (event.type === EventType.STATE_SNAPSHOT) {
          const state = (event as { snapshot?: unknown }).snapshot as
            | GraphExploreState
            | undefined;
          if (state?.canvas) {
            const c = state.canvas;
            // 指纹必须覆盖所有驱动前端副作用的字段，否则纯视图切换 / 着色
            // （object_count / edges / last_query_summary 不变）会被当成重复
            // 事件丢弃，导致 switch_view / color_by 不生效（画布视图不切、
            // 颜色不更新）。view + color_by + expanded_links 与数据字段并列纳入。
            const fingerprint = `${c.object_count}|${c.edges?.length ?? 0}|${c.last_query_summary}|${c.view ?? ''}|${c.color_by ?? ''}|${(c.expanded_links ?? []).join(',')}`;
            if (fingerprint === this.lastSnapshotFingerprint) return;
            this.lastSnapshotFingerprint = fingerprint;
            this.onCanvasState(c);
          }
        }
      }),
    );
  }
}

interface UseGraphExploreAgentArgs {
  ontology: string;
  explore: ReturnType<typeof useGraphExplore>;
  /** Called when the Agent drives a canvas state change. Wires
   *  state.canvas → useGraphExplore (loadStartSet / setView / setLayerStyle). */
  onCanvasState: (canvas: CanvasSnapshot, explore: ReturnType<typeof useGraphExplore>) => void;
}

/** Build an AG-UI agent scoped to the open ontology, with a tap that drives
 *  the canvas from STATE_SNAPSHOT events. Pass the returned agent to
 *  <AssistantUiChat agent={...} /> — it builds the runtime internally.
 *
 * Agent 只在 ontology 变化时重建（避免 explore/onCanvasState 引用每帧变化
 * 导致 agent 重建 → SSE 重订阅 → 死循环）。最新的 explore/onCanvasState
 * 通过 ref 传入，agent 闭包读 ref.current。 */
export function useGraphExploreAgent({ ontology, explore, onCanvasState }: UseGraphExploreAgentArgs) {
  // ref 持有最新的 explore/onCanvasState，agent 闭包读 ref.current，
  // 避免 agent 因 explore 引用变化而重建。
  const exploreRef = useRef(explore);
  exploreRef.current = explore;
  const onCanvasStateRef = useRef(onCanvasState);
  onCanvasStateRef.current = onCanvasState;

  const agent = useMemo(
    () =>
      new GraphExploreAgent(
        { url: '/ai/agent', headers: { Accept: 'text/event-stream' } },
        ontology,
        (canvas) => onCanvasStateRef.current(canvas, exploreRef.current),
      ),
 // ontology 是唯一重建触发条件（上下文切换 = 新会话）
    [ontology],
  );

  return { agent };
}
