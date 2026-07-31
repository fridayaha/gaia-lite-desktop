/**
 * usePipelineBuilderAgent — AG-UI Agent 驱动 Pipeline Builder 画布（ADR-018 §14.5）。
 *
 * 复用图探索的 AG-UI Agent 模式（ADR-015）：HttpAgent 子类 tap SSE 事件流，
 * 拦截 STATE_SNAPSHOT → 解析 state.pipeline_canvas → 驱动画布。
 *
 * AI 通过 pipeline_builder toolset（8 个工具）操纵画布：
 * - list_datasets / get_dataset_schema（查询）
 * - add_source / add_transform / add_sink（添加节点）
 * - modify_node / remove_node / connect（编辑拓扑）
 *
 * 每个工具执行后发 STATE_SNAPSHOT，前端逐个更新节点。
 *
 * 死循环防护（复用图探索三层）：
 * 1. SSE tap 指纹去重（ag-ui runtime re-render 重放同一 snapshot 不重复处理）
 * 2. applyingRef 防并发（异步水合期间不重入）
 * 3. lastAppliedFingerprint 兜底去重
 */
import { useMemo, useRef } from 'react';
import { HttpAgent, type RunAgentInput, type BaseEvent, EventType } from '@ag-ui/client';
import { tap } from 'rxjs';

/** Pipeline 画布快照（Agent 共享状态，与后端 PipelineCanvasState 对齐）。 */
export interface PipelineCanvasSnapshot {
  nodes: Array<{
    id: string;
    type: string;
    operator_type: string;
    label: string;
    config: Record<string, unknown>;
    position: { x: number; y: number };
  }>;
  edges: Array<{
    id: string;
    source_id: string;
    target_id: string;
  }>;
  selected_node_id: string | null;
}

export interface PipelineBuilderAgentState {
  pipeline_canvas: PipelineCanvasSnapshot;
}

/** 画布更新回调类型。 */
export type OnPipelineCanvasState = (
  canvas: PipelineCanvasSnapshot,
) => void;

/** HttpAgent 子类：注入 ontology + tap STATE_SNAPSHOT 驱动画布。 */
class PipelineBuilderAgent extends HttpAgent {
  private readonly ontology: string;
  private readonly onCanvasState: OnPipelineCanvasState;
  private lastSnapshotFingerprint = '';

  constructor(
    config: ConstructorParameters<typeof HttpAgent>[0],
    ontology: string,
    onCanvasState: OnPipelineCanvasState,
  ) {
    super(config);
    this.ontology = ontology;
    this.onCanvasState = onCanvasState;
  }

  protected override prepareRunAgentInput(parameters?: Parameters<HttpAgent['runAgent']>[0]): RunAgentInput {
    const input = super.prepareRunAgentInput(parameters);
    return {
      ...input,
      forwardedProps: {
        ...(input.forwardedProps ?? {}),
        ontology: this.ontology,
        mode: 'pipeline_builder',
      },
    };
  }

  override run(input: RunAgentInput) {
    const stream = super.run(input);
    return stream.pipe(
      tap((event: BaseEvent) => {
        if (event.type === EventType.STATE_SNAPSHOT) {
          const state = (event as { snapshot?: unknown }).snapshot as
            | PipelineBuilderAgentState
            | undefined;
          if (state?.pipeline_canvas) {
            const canvas = state.pipeline_canvas;
            // 指纹去重：避免 runtime re-render 重放导致死循环
            const nodeIds = canvas.nodes.map((n) => n.id).sort().join(',');
            const edgeIds = canvas.edges.map((e) => e.id).sort().join(',');
            const fingerprint = `${nodeIds}|${edgeIds}|${canvas.selected_node_id ?? ''}`;
            if (fingerprint === this.lastSnapshotFingerprint) return;
            this.lastSnapshotFingerprint = fingerprint;
            this.onCanvasState(canvas);
          }
        }
      }),
    );
  }
}

interface UsePipelineBuilderAgentArgs {
  ontology: string;
  /** 当 Agent 驱动画布状态变化时回调。接收画布快照，需要同步到 Zustand store。 */
  onCanvasState: OnPipelineCanvasState;
}

/** 创建 AG-UI Agent，作用域到当前 ontology，tap STATE_SNAPSHOT 驱动画布。
 *
 * Agent 只在 ontology 变化时重建（上下文切换=新会话）。
 * onCanvasState 通过 ref 传入，避免因引用变化导致 agent 重建。
 */
export function usePipelineBuilderAgent({
  ontology,
  onCanvasState,
}: UsePipelineBuilderAgentArgs) {
  const onCanvasStateRef = useRef(onCanvasState);
  onCanvasStateRef.current = onCanvasState;

  const agent = useMemo(
    () =>
      new PipelineBuilderAgent(
        { url: '/ai/agent', headers: { Accept: 'text/event-stream' } },
        ontology,
        (canvas) => onCanvasStateRef.current(canvas),
      ),
    [ontology],
  );

  return { agent };
}
