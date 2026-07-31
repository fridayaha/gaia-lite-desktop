import { describe, it, expect } from 'vitest';
import { shouldRelayout } from '../../lib/graphLayout';

/**
 * 图谱布局重排决策测试。
 *
 * 覆盖核心回归场景：切换本体时，objectTypes 与 links 若分两次到达，
 * 第二次（仅新增边、节点不变）也必须重排，否则边加在「无边时排成的
 * 网格」位置上，节点挤在一起、关系看不到。
 */
describe('shouldRelayout', () => {
  const base = {
    isFirstSync: false,
    hasNewNodes: false,
    hasNewEdges: false,
    containerVisible: true,
    nodeCount: 5,
  };

  it('首次同步且有节点时重排', () => {
    expect(shouldRelayout({ ...base, isFirstSync: true, nodeCount: 5 })).toBe(true);
  });

  it('首次同步但无节点时不重排', () => {
    expect(shouldRelayout({ ...base, isFirstSync: true, nodeCount: 0 })).toBe(false);
  });

  it('容器不可见时永不重排（即使首次+有新节点）', () => {
    expect(
      shouldRelayout({
        ...base,
        containerVisible: false,
        isFirstSync: true,
        hasNewNodes: true,
        nodeCount: 5,
      }),
    ).toBe(false);
  });

  it('有新节点时重排（非首次）', () => {
    expect(shouldRelayout({ ...base, hasNewNodes: true })).toBe(true);
  });

  it('有新边但无新节点时也重排（切换本体回归 bug 的核心防护）', () => {
    // 场景：objectTypes 先到（已建节点），links 后到（仅新增边）。
    // 若不重排，边会加在「无边上一次布局」的网格位置，节点挤在一起。
    expect(shouldRelayout({ ...base, hasNewEdges: true, hasNewNodes: false })).toBe(true);
  });

  it('节点和边都无变化时不重排（保留用户拖拽/缩放）', () => {
    expect(shouldRelayout({ ...base })).toBe(false);
  });

  it('新节点 + 新边同时存在时重排', () => {
    expect(shouldRelayout({ ...base, hasNewNodes: true, hasNewEdges: true })).toBe(true);
  });
});
