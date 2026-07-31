/**
 * nodeAlignment 单元测试 — 对齐与分布几何计算。
 */
import { describe, it, expect } from 'vitest';
import { alignNodes, distributeNodes } from '../nodeAlignment';
import type { IRNode } from '../../../types/pipeline';

function makeNode(id: string, x: number, y: number, w = 100, h = 50): IRNode {
  return {
    id,
    type: 'Transform',
    operator_type: 'Filter',
    label: id,
    description: '',
    input_schemas: [],
    output_schema: null,
    config: {} as any,
    position: { x, y },
    measured: { width: w, height: h },
  };
}

describe('alignNodes', () => {
  it('节点数 < 2 时返回空', () => {
    expect(alignNodes([makeNode('a', 0, 0)], 'left')).toEqual([]);
  });

  it('left: 所有节点 x 对齐到最小 x', () => {
    const nodes = [makeNode('a', 100, 0), makeNode('b', 50, 100), makeNode('c', 200, 200)];
    const result = alignNodes(nodes, 'left');
    expect(result.every((r) => r.position.x === 50)).toBe(true);
  });

  it('right: 所有节点右边对齐到最大 right', () => {
    // a: x=100,w=100 → right=200; b: x=50,w=100 → right=150; c: x=200,w=100 → right=300
    // max right=300，对齐后所有 right=300
    const nodes = [makeNode('a', 100, 0, 100, 50), makeNode('b', 50, 100, 100, 50), makeNode('c', 200, 200, 100, 50)];
    const result = alignNodes(nodes, 'right');
    // c 不动（right 已是 300），a.x=200, b.x=200
    const map = new Map(result.map((r) => [r.id, r.position.x]));
    expect(map.get('a')).toBe(200);
    expect(map.get('b')).toBe(200);
    expect(map.get('c')).toBe(200);
  });

  it('centerH: 水平中心对齐', () => {
    // minX=50, maxX(右边)=300, center=175
    const nodes = [makeNode('a', 100, 0, 100, 50), makeNode('b', 50, 100, 100, 50), makeNode('c', 200, 200, 100, 50)];
    const result = alignNodes(nodes, 'centerH');
    // 中心 175，节点宽 100，x = 175 - 50 = 125
    expect(result.every((r) => r.position.x === 125)).toBe(true);
  });

  it('top: 所有节点 y 对齐到最小 y', () => {
    const nodes = [makeNode('a', 0, 100), makeNode('b', 0, 50), makeNode('c', 0, 200)];
    const result = alignNodes(nodes, 'top');
    expect(result.every((r) => r.position.y === 50)).toBe(true);
  });

  it('bottom: 底边对齐到最大 bottom', () => {
    // a: y=100,h=50→bottom=150; b: y=50→100; c: y=200→250; max=250
    const nodes = [makeNode('a', 0, 100, 100, 50), makeNode('b', 0, 50, 100, 50), makeNode('c', 0, 200, 100, 50)];
    const result = alignNodes(nodes, 'bottom');
    const map = new Map(result.map((r) => [r.id, r.position.y]));
    expect(map.get('a')).toBe(200);
    expect(map.get('b')).toBe(200);
    expect(map.get('c')).toBe(200);
  });

  it('centerV: 垂直中心对齐', () => {
    // minY=50, maxY(bottom)=250, center=150, h=50 → y=125
    const nodes = [makeNode('a', 0, 100, 100, 50), makeNode('b', 0, 50, 100, 50), makeNode('c', 0, 200, 100, 50)];
    const result = alignNodes(nodes, 'centerV');
    expect(result.every((r) => r.position.y === 125)).toBe(true);
  });

  it('对齐时保持未变化维度不变', () => {
    const nodes = [makeNode('a', 100, 100), makeNode('b', 50, 200)];
    const result = alignNodes(nodes, 'left');
    const map = new Map(result.map((r) => [r.id, r.position]));
    // left 对齐只改 x，y 保持原值
    expect(map.get('a')?.y).toBe(100);
    expect(map.get('b')?.y).toBe(200);
  });

  it('无 measured 时用 fallback 尺寸', () => {
    const nodes = [
      { ...makeNode('a', 100, 0), measured: undefined },
      { ...makeNode('b', 50, 0), measured: undefined },
    ];
    // 不崩溃即可
    const result = alignNodes(nodes, 'left');
    expect(result.length).toBe(2);
  });
});

describe('distributeNodes', () => {
  it('节点数 < 3 时返回空', () => {
    expect(distributeNodes([makeNode('a', 0, 0), makeNode('b', 100, 0)], 'horizontal')).toEqual([]);
  });

  it('horizontal: 水平等距分布', () => {
    // 三个节点 x=0, 100, 400，first=0, last=400, step=200
    const nodes = [makeNode('a', 100, 0), makeNode('b', 0, 0), makeNode('c', 400, 0)];
    const result = distributeNodes(nodes, 'horizontal');
    // 排序后 a(x=100) 是中间，但 sort 后顺序是 b(0), a(100), c(400)
    // first=0, last=400, step=200 → 0, 200, 400
    const sorted = [...result].sort((p, q) => p.position.x - q.position.x);
    expect(sorted[0].position.x).toBe(0);
    expect(sorted[1].position.x).toBe(200);
    expect(sorted[2].position.x).toBe(400);
  });

  it('vertical: 垂直等距分布', () => {
    const nodes = [makeNode('a', 0, 100), makeNode('b', 0, 0), makeNode('c', 0, 400)];
    const result = distributeNodes(nodes, 'vertical');
    const sorted = [...result].sort((p, q) => p.position.y - q.position.y);
    expect(sorted[0].position.y).toBe(0);
    expect(sorted[1].position.y).toBe(200);
    expect(sorted[2].position.y).toBe(400);
  });

  it('分布时保持非分布轴坐标不变', () => {
    const nodes = [makeNode('a', 5, 0), makeNode('b', 99, 0), makeNode('c', 7, 400)];
    const result = distributeNodes(nodes, 'horizontal');
    const map = new Map(result.map((r) => [r.id, r.position]));
    // 水平分布只改 x，y 不变
    expect(map.get('a')?.y).toBe(0);
    expect(map.get('b')?.y).toBe(0);
    expect(map.get('c')?.y).toBe(400);
  });
});
