/**
 * useSearchAroundConfig hook 测试（多步 Search Around IR 构建）。
 */
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { renderHook, act } from '@testing-library/react';
import { useSearchAroundConfig } from '../useSearchAroundConfig';

vi.mock('../../api/graph', () => ({
  traverseLink: vi.fn(),
  queryDataFrame: vi.fn(),
}));

import { traverseLink } from '../../api/graph';
const mockTraverse = traverseLink as ReturnType<typeof vi.fn>;

describe('useSearchAroundConfig', () => {
  beforeEach(() => vi.clearAllMocks());

  it('初始状态为空', () => {
    const { result } = renderHook(() => useSearchAroundConfig());
    expect(result.current.startVids).toHaveLength(0);
    expect(result.current.steps).toHaveLength(0);
    expect(result.current.buildIR()).toBeNull();
  });

  it('setStart 设置起始集', () => {
    const { result } = renderHook(() => useSearchAroundConfig());
    act(() => result.current.setStart(['S001', 'S002']));
    expect(result.current.startVids).toEqual(['S001', 'S002']);
  });

  it('addStep + updateStep 配置跳', () => {
    const { result } = renderHook(() => useSearchAroundConfig());
    act(() => result.current.setStart(['S001']));
    act(() => result.current.addStep());
    expect(result.current.steps).toHaveLength(1);
    const stepId = result.current.steps[0].id;
    act(() => result.current.updateStep(stepId, { linkType: 'supplies', maxHops: 3 }));
    expect(result.current.steps[0].linkType).toBe('supplies');
    expect(result.current.steps[0].maxHops).toBe(3);
  });

  it('buildIR 构建嵌套 searchAround IR', () => {
    const { result } = renderHook(() => useSearchAroundConfig());
    act(() => result.current.setStart(['S001']));
    act(() => result.current.addStep());
    const id1 = result.current.steps[0].id;
    act(() => result.current.updateStep(id1, { linkType: 'supplies', direction: 'forward', maxHops: 2 }));
    act(() => result.current.addStep());
    const id2 = result.current.steps[1].id;
    act(() => result.current.updateStep(id2, { linkType: 'produces', direction: 'reverse', maxHops: 1 }));

    const ir = result.current.buildIR()!;
    expect(ir.type).toBe('searchAround');
    expect(ir.link).toBe('produces');
    expect(ir.direction).toBe('in');
    expect(ir.hops).toEqual([1, 1]);
    // 内层是第一跳
    expect(ir.object_set?.type).toBe('searchAround');
    expect(ir.object_set?.link).toBe('supplies');
    expect(ir.object_set?.direction).toBe('out');
    expect(ir.object_set?.object_set?.type).toBe('static');
  });

  it('未配 linkType 的跳被过滤', () => {
    const { result } = renderHook(() => useSearchAroundConfig());
    act(() => result.current.setStart(['S001']));
    act(() => result.current.addStep()); // 空 linkType
    expect(result.current.buildIR()).toBeNull();
  });

  it('removeStep 移除该跳及之后', () => {
    const { result } = renderHook(() => useSearchAroundConfig());
    act(() => result.current.addStep());
    act(() => result.current.addStep());
    act(() => result.current.addStep());
    expect(result.current.steps).toHaveLength(3);
    const id2 = result.current.steps[1].id;
    act(() => result.current.removeStep(id2));
    expect(result.current.steps).toHaveLength(1); // 只剩第一跳
  });

  it('previewStep 调 traverseLink 获取命中数', async () => {
    mockTraverse.mockResolvedValue({
      target_objects: [{ rid: 'O1' }, { rid: 'O2' }, { rid: 'O3' }],
    });
    const { result } = renderHook(() => useSearchAroundConfig());
    act(() => result.current.setStart(['S001']));
    act(() => result.current.addStep());
    const id = result.current.steps[0].id;
    act(() => result.current.updateStep(id, { linkType: 'supplies' }));

    await act(async () => {
      await result.current.previewStep('ONT', id);
    });
    expect(result.current.steps[0].previewCount).toBe(3);
  });

  it('reset 清空所有', () => {
    const { result } = renderHook(() => useSearchAroundConfig());
    act(() => result.current.setStart(['S001']));
    act(() => result.current.addStep());
    act(() => result.current.reset());
    expect(result.current.startVids).toHaveLength(0);
    expect(result.current.steps).toHaveLength(0);
  });
});
