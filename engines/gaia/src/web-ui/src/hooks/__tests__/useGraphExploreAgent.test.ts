/**
 * useGraphExploreAgent 测试 — AG-UI Agent 驱动画布（ADR-015）。
 *
 * 重点覆盖 SSE STATE_SNAPSHOT 指纹去重逻辑：
 *  - BUG 2 回归：switch_view / color_by 产生的 snapshot 与上一步数据完全相同
 *    （object_count / edges / last_query_summary 不变），若指纹不含 view/color_by
 *    会被当成重复事件丢弃 → onCanvasState 不触发 → 画布视图不切、颜色不更新。
 *    修复后指纹纳入 view + color_by + expanded_links，纯视图/着色切换必须触发回调。
 */
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { renderHook } from '@testing-library/react';
import { from, type Observable } from 'rxjs';
import { EventType, type BaseEvent, type RunAgentInput } from '@ag-ui/client';

// mock @ag-ui/client 的 HttpAgent —— 只需要 run() 返回我们构造的事件流。
// GraphExploreAgent extends HttpAgent，super.run(input) 返回 mock stream。
vi.mock('@ag-ui/client', async () => {
  const actual = await vi.importActual<typeof import('@ag-ui/client')>('@ag-ui/client');
  class MockHttpAgent {
    url: string;
    headers: Record<string, string>;
    // 动态替换的目标：测试里覆盖 prototype.run 返回构造的事件流。
    run(_input: RunAgentInput): Observable<BaseEvent> {
      return from([] as BaseEvent[]);
    }
    constructor(config: { url: string; headers?: Record<string, string> }) {
      this.url = config.url;
      this.headers = config.headers ?? {};
    }
  }
  return { ...actual, HttpAgent: MockHttpAgent };
});

import { useGraphExploreAgent } from '../useGraphExploreAgent';
import type { CanvasSnapshot } from '../../types/canvas';
import type { useGraphExplore } from '../useGraphExplore';

/** 构造 STATE_SNAPSHOT 事件，snapshot.canvas 为给定值。 */
function stateEvent(canvas: Partial<CanvasSnapshot>): BaseEvent {
  return {
    type: EventType.STATE_SNAPSHOT,
    snapshot: { canvas },
  } as unknown as BaseEvent;
}

/** 最小化的 explore mock —— useGraphExploreAgent 只用 ref 持有它，不调其方法。 */
function mockExplore() {
  return { ontology: 'TestOnt' } as unknown as ReturnType<typeof useGraphExplore>;
}

/** 让 agent 的 super.run（HttpAgent.prototype.run）返回给定事件流，
 *  然后触发子类 run（带 tap），等流结束后 resolve。 */
async function emitEvents(
  agent: { run: (i: RunAgentInput) => Observable<BaseEvent> },
  events: BaseEvent[],
): Promise<void> {
  // 覆盖父类 prototype.run（GraphExploreAgent.run 调 super.run）。
  const proto = Object.getPrototypeOf(Object.getPrototypeOf(agent)) as { run: unknown };
  proto.run = () => from(events);
  await new Promise<void>((resolve) => {
    agent.run({} as RunAgentInput).subscribe({ complete: () => resolve() });
  });
}

/** renderHook 包装 useGraphExploreAgent（它内部用了 useRef/useMemo）。 */
function renderAgent(onCanvasState: ReturnType<typeof vi.fn>) {
  const explore = mockExplore();
  const { result } = renderHook(() =>
    useGraphExploreAgent({
      ontology: 'TestOnt',
      explore,
      onCanvasState: onCanvasState as (canvas: CanvasSnapshot, explore: ReturnType<typeof useGraphExplore>) => void,
    }),
  );
  return result.current.agent as unknown as {
    run: (i: RunAgentInput) => Observable<BaseEvent>;
  };
}

describe('useGraphExploreAgent — STATE_SNAPSHOT 指纹去重', () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it('指纹纳入 view：纯视图切换的 snapshot 不被去重（BUG 2 回归）', async () => {
    // 模拟 BUG 2：先加载 50 供应商（view=graph），再 switch_view 到 map。
    // 两个 snapshot 数据完全相同，仅 view 不同 → 都必须触发 onCanvasState。
    const onCanvasState = vi.fn();
    const agent = renderAgent(onCanvasState);

    const base: Partial<CanvasSnapshot> = {
      object_count: 50,
      edges: [],
      last_query_summary: 'Supplier (50 个对象)',
      view: 'graph',
      color_by: null,
      expanded_links: [],
    };
    const viewMap: Partial<CanvasSnapshot> = { ...base, view: 'map' };
    const colorBy: Partial<CanvasSnapshot> = { ...base, view: 'map', color_by: 'riskLevel' };

    await emitEvents(agent, [stateEvent(base), stateEvent(viewMap), stateEvent(colorBy)]);

    // 修复前：指纹不含 view/color_by → 三个指纹相同 → 只触发 1 次。
    // 修复后：指纹含 view/color_by → 三个指纹各不同 → 触发 3 次。
    expect(onCanvasState).toHaveBeenCalledTimes(3);
  });

  it('完全相同的 snapshot 被去重（只触发一次）', async () => {
    const onCanvasState = vi.fn();
    const agent = renderAgent(onCanvasState);

    const snapshot: Partial<CanvasSnapshot> = {
      object_count: 3,
      edges: [{ source_rid: 'a', target_rid: 'b', link_type: 'lt', direction: 'out' }],
      last_query_summary: 'test',
      view: 'graph',
      color_by: null,
      expanded_links: [],
    };

    await emitEvents(agent, [stateEvent(snapshot), stateEvent(snapshot)]);

    // 完全相同 → 第二个被指纹去重（防止 runtime re-render 重放事件流死循环）
    expect(onCanvasState).toHaveBeenCalledTimes(1);
  });

  it('expanded_links 变化也触发回调（纳入指纹）', async () => {
    const onCanvasState = vi.fn();
    const agent = renderAgent(onCanvasState);

    const base: Partial<CanvasSnapshot> = {
      object_count: 2,
      edges: [],
      last_query_summary: 'test',
      view: 'graph',
      color_by: null,
      expanded_links: [],
    };
    const afterExpand: Partial<CanvasSnapshot> = {
      ...base,
      expanded_links: ['supplies'],
    };

    await emitEvents(agent, [stateEvent(base), stateEvent(afterExpand)]);

    expect(onCanvasState).toHaveBeenCalledTimes(2);
  });
});
