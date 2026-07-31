/**
 * useGraphExplore hook 测试（图探索画布状态管理）。
 *
 * 覆盖：loadStartSet / searchAround / undo / removeNode / clear / LOD 折叠 / 截断。
 */
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { renderHook, act } from '@testing-library/react';
import { useGraphExplore } from '../useGraphExplore';
import type { ReasoningResult, TraverseResult } from '../../types';

// mock graph API
vi.mock('../../api/graph', () => ({
  queryDataFrame: vi.fn(),
  traverseLink: vi.fn(),
}));

import { queryDataFrame, traverseLink } from '../../api/graph';

const mockQuery = queryDataFrame as ReturnType<typeof vi.fn>;
const mockTraverse = traverseLink as ReturnType<typeof vi.fn>;

const ONT = 'TestOnt';

function makeResult(rids: string[], truncated = false): ReasoningResult {
  return {
    objects: rids.map((rid) => ({
      rid,
      api_name: 'Supplier',
      props: { name: rid, supplierId: rid },
    })),
    truncated,
    aggregates: [],
    next_cursor: null,
    stats: { steps: 1, engines_used: ['postgres'], timings: {}, total_vids: rids.length, hydrated: rids.length },
    evidence_id: 'evi-1',
  } as unknown as ReasoningResult;
}

function makeTraverse(targets: string[]): TraverseResult {
  return {
    target_objects: targets.map((rid) => ({
      rid,
      api_name: 'Order',
      props: { orderId: rid },
    })),
    source_to_target_map: {},
  } as unknown as TraverseResult;
}

describe('useGraphExplore', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    localStorage.clear();
  });

  it('loadStartSet 装载节点 + 设置证据', async () => {
    mockQuery.mockResolvedValue(makeResult(['S001', 'S002']));
    const { result } = renderHook(() => useGraphExplore(ONT));

    await act(async () => {
      await result.current.loadStartSet({ type: 'objectType', object_type: 'Supplier' });
    });

    expect(result.current.nodeCount).toBe(2);
    expect(result.current.lastEvidenceId).toBe('evi-1');
    expect(result.current.error).toBeNull();
  });

  it('loadStartSet 失败时设置 error', async () => {
    mockQuery.mockRejectedValue(new Error('网络错误'));
    const { result } = renderHook(() => useGraphExplore(ONT));

    await act(async () => {
      await result.current.loadStartSet({ type: 'objectType', object_type: 'Supplier' });
    });

    expect(result.current.error).toBe('网络错误');
    expect(result.current.nodeCount).toBe(0);
  });

  it('searchAround 增量加节点 + 边', async () => {
    mockQuery.mockResolvedValue(makeResult(['S001']));
    mockTraverse.mockResolvedValue(makeTraverse(['O1', 'O2']));
    const { result } = renderHook(() => useGraphExplore(ONT));

    await act(async () => {
      await result.current.loadStartSet({ type: 'objectType', object_type: 'Supplier' });
    });
    await act(async () => {
      await result.current.searchAround('S001', 'supplies', 'forward');
    });

    expect(result.current.nodeCount).toBe(3); // S001 + O1 + O2
    expect(result.current.edgeCount).toBe(2); // S001->O1, S001->O2
  });

  it('undo 撤销最后一次 searchAround', async () => {
    mockQuery.mockResolvedValue(makeResult(['S001']));
    mockTraverse.mockResolvedValue(makeTraverse(['O1']));
    const { result } = renderHook(() => useGraphExplore(ONT));

    await act(async () => {
      await result.current.loadStartSet({ type: 'objectType', object_type: 'Supplier' });
    });
    await act(async () => {
      await result.current.searchAround('S001', 'supplies', 'forward');
    });
    expect(result.current.nodeCount).toBe(2);

    act(() => result.current.undo());
    expect(result.current.nodeCount).toBe(1); // 只剩 S001
    expect(result.current.edgeCount).toBe(0);
  });

  it('removeNode 移除节点 + 关联边', async () => {
    mockQuery.mockResolvedValue(makeResult(['S001', 'S002']));
    const { result } = renderHook(() => useGraphExplore(ONT));

    await act(async () => {
      await result.current.loadStartSet({ type: 'objectType', object_type: 'Supplier' });
    });

    act(() => result.current.removeNode('S001'));
    expect(result.current.nodeCount).toBe(1);
    expect(result.current.nodes.has('S001')).toBe(false);
    expect(result.current.nodes.has('S002')).toBe(true);
  });

  it('clear 清空所有节点/边/选中', async () => {
    mockQuery.mockResolvedValue(makeResult(['S001']));
    const { result } = renderHook(() => useGraphExplore(ONT));

    await act(async () => {
      await result.current.loadStartSet({ type: 'objectType', object_type: 'Supplier' });
    });
    act(() => result.current.setSelectedVid('S001'));

    act(() => result.current.clear());
    expect(result.current.nodeCount).toBe(0);
    expect(result.current.edgeCount).toBe(0);
    expect(result.current.selectedVid).toBeNull();
  });

  it('truncated 状态从结果同步', async () => {
    mockQuery.mockResolvedValue(makeResult(['S001'], true));
    const { result } = renderHook(() => useGraphExplore(ONT));

    await act(async () => {
      await result.current.loadStartSet({ type: 'objectType', object_type: 'Supplier' });
    });

    expect(result.current.truncated).toBe(true);
  });

  it('LOD 折叠：节点超 500 触发 shouldCollapse', async () => {
    const rids = Array.from({ length: 501 }, (_, i) => `S${String(i).padStart(3, '0')}`);
    mockQuery.mockResolvedValue(makeResult(rids));
    const { result } = renderHook(() => useGraphExplore(ONT));

    await act(async () => {
      await result.current.loadStartSet({ type: 'objectType', object_type: 'Supplier' });
    });

    expect(result.current.shouldCollapse).toBe(true);
    expect(result.current.overLimit).toBe(false); // 501 < 2000
  });

  it('layerStyle 从 localStorage 恢复', () => {
    localStorage.setItem('gaia:layerStyle', JSON.stringify({ colorBy: 'property', colorProp: 'status', sizeBy: 'degree' }));
    const { result } = renderHook(() => useGraphExplore(ONT));
    expect(result.current.layerStyle.colorBy).toBe('property');
    expect(result.current.layerStyle.colorProp).toBe('status');
    expect(result.current.layerStyle.sizeBy).toBe('degree');
  });

  it('setLayerStyle 持久化到 localStorage', () => {
    const { result } = renderHook(() => useGraphExplore(ONT));
    act(() => result.current.setLayerStyle({ colorBy: 'property', colorProp: 'x', sizeBy: 'fixed' }));
    const saved = JSON.parse(localStorage.getItem('gaia:layerStyle') || '{}');
    expect(saved.colorBy).toBe('property');
    expect(saved.colorProp).toBe('x');
  });

  it('applyCanvasSnapshot 探索查询：增量合并节点 + 边（轨迹）', async () => {
    // 第一步纯查询加载 S000
    mockQuery.mockResolvedValueOnce(makeResult(['S000']));
    const { result } = renderHook(() => useGraphExplore(ONT));
    await act(async () => {
      await result.current.loadStartSet({ type: 'objectType', object_type: 'Supplier' });
    });
    expect(result.current.nodeCount).toBe(1);

    // 第二步 searchAround：canvas 带 edges → 水合 M1/M2 + 合并边
    mockQuery.mockResolvedValueOnce(makeResult(['M1', 'M2']));
    await act(async () => {
      await result.current.applyCanvasSnapshot({
        objects: [
          { rid: 'M1', api_name: 'Material', title: '螺丝', summary: {} },
          { rid: 'M2', api_name: 'Material', title: '螺母', summary: {} },
        ],
        edges: [
          { source_rid: 'S000', target_rid: 'M1', link_type: 'supplies', direction: 'out' as const },
          { source_rid: 'S000', target_rid: 'M2', link_type: 'supplies', direction: 'out' as const },
        ],
        view: 'graph',
        color_by: null,
        expanded_links: ['supplies'],
        object_count: 2,
        last_query_summary: 'Material (2)',
      });
    });

    // S000 保留 + M1/M2 累积
    expect(result.current.nodeCount).toBe(3);
    // 边合并
    expect(result.current.edgeCount).toBe(2);
  });

  it('applyCanvasSnapshot 纯查询：覆盖节点 + 清空边', async () => {
    // 先探索出有节点+边
    mockQuery.mockResolvedValueOnce(makeResult(['S000']));
    const { result } = renderHook(() => useGraphExplore(ONT));
    await act(async () => {
      await result.current.loadStartSet({ type: 'objectType', object_type: 'Supplier' });
    });
    mockQuery.mockResolvedValueOnce(makeResult(['M1']));
    await act(async () => {
      await result.current.applyCanvasSnapshot({
        objects: [{ rid: 'M1', api_name: 'Material', title: '', summary: {} }],
        edges: [{ source_rid: 'S000', target_rid: 'M1', link_type: 'supplies', direction: 'out' as const }],
        view: 'graph', color_by: null, expanded_links: ['supplies'],
        object_count: 1, last_query_summary: 'Material (1)',
      });
    });
    expect(result.current.nodeCount).toBe(2);
    expect(result.current.edgeCount).toBe(1);

    // 纯查询（无 edges）→ 覆盖刷新，清空边
    mockQuery.mockResolvedValueOnce(makeResult(['O1']));
    await act(async () => {
      await result.current.applyCanvasSnapshot({
        objects: [{ rid: 'O1', api_name: 'Order', title: '', summary: {} }],
        edges: [],
        view: 'graph', color_by: null, expanded_links: [],
        object_count: 1, last_query_summary: 'Order (1)',
      });
    });
    expect(result.current.nodeCount).toBe(1); // 只剩 O1
    expect(result.current.edgeCount).toBe(0); // 边清空
  });

  it('applyCanvasSnapshot 边去重：同一条边不重复', async () => {
    mockQuery.mockResolvedValue(makeResult(['M1']));
    const { result } = renderHook(() => useGraphExplore(ONT));
    const canvas = {
      objects: [{ rid: 'M1', api_name: 'Material', title: '', summary: {} }],
      edges: [{ source_rid: 'S000', target_rid: 'M1', link_type: 'supplies', direction: 'out' as const }],
      view: 'graph' as const, color_by: null, expanded_links: ['supplies'],
      object_count: 1, last_query_summary: 'M1',
    };
    await act(async () => {
      await result.current.applyCanvasSnapshot(canvas);
    });
    await act(async () => {
      await result.current.applyCanvasSnapshot(canvas); // 再次应用同一条边
    });
    expect(result.current.edgeCount).toBe(1); // 不重复
  });
});
