/**
 * usePipelineBuilder store 测试。
 *
 * 重点覆盖 onNodesChange 的 select 处理 —— 旧实现用 changes.find 只取第一条
 * select change，导致「取消旧选中 + 选中新节点」两条 change 同时派发时，
 * 新节点的选中被丢弃，selectedNodeId 被错误置空（右侧配置面板闪退 bug）。
 */
import { describe, it, expect, beforeEach } from 'vitest';
import { usePipelineBuilder } from '../usePipelineBuilder';
import type { NodeChange, EdgeChange } from '@xyflow/react';
import type { OperatorType } from '../../types/pipeline';

// 构造一个最小 IRNode，仅含 onNodesChange 关心的字段
function makeNode(id: string, operatorType: OperatorType = 'Filter') {
  return {
    id,
    type: 'Transform' as const,
    operator_type: operatorType,
    label: id,
    description: '',
    input_schemas: [],
    output_schema: null,
    config: {},
    position: { x: 0, y: 0 },
  };
}

describe('usePipelineBuilder.onNodesChange — select 处理', () => {
  beforeEach(() => {
    usePipelineBuilder.setState({
      irNodes: [makeNode('A'), makeNode('B')],
      irEdges: [],
      selectedNodeId: null,
      isDirty: false,
    });
  });

  it('select change 写入多选集合 selectedNodeIds，但不影响单选 selectedNodeId', () => {
    // 受控模式下程序化 setNodes 会触发虚假 select change（xyflow #2405），
    // onNodesChange 不动 selectedNodeId（仍由 onNodeClick 管理），
    // 但会把框选/Shift+点选的 select change 写入 selectedNodeIds（多选集合）。
    usePipelineBuilder.setState({ selectedNodeId: 'A', selectedNodeIds: [] });
    const changes: NodeChange[] = [
      { id: 'A', type: 'select', selected: false },
      { id: 'B', type: 'select', selected: true },
    ];
    usePipelineBuilder.getState().onNodesChange(changes);
    // selectedNodeId 不受 onNodesChange 影响
    expect(usePipelineBuilder.getState().selectedNodeId).toBe('A');
    // B 被加入多选集合
    expect(usePipelineBuilder.getState().selectedNodeIds).toContain('B');
  });

  it('多选 ≥2 个时清单选 selectedNodeId（避免单选与多选高亮并存）', () => {
    usePipelineBuilder.setState({ selectedNodeId: 'A', selectedNodeIds: [] });
    const changes: NodeChange[] = [
      { id: 'A', type: 'select', selected: true },
      { id: 'B', type: 'select', selected: true },
    ];
    usePipelineBuilder.getState().onNodesChange(changes);
    expect(usePipelineBuilder.getState().selectedNodeId).toBeNull();
    expect(usePipelineBuilder.getState().selectedNodeIds).toHaveLength(2);
  });

  it('无 select change 时不影响当前选中态', () => {
    usePipelineBuilder.setState({ selectedNodeId: 'A' });
    const changes: NodeChange[] = [
      { id: 'A', type: 'position', position: { x: 10, y: 20 }, dragging: false },
    ];
    usePipelineBuilder.getState().onNodesChange(changes);
    expect(usePipelineBuilder.getState().selectedNodeId).toBe('A');
  });
});

describe('usePipelineBuilder.onNodesChange — position 变更', () => {
  beforeEach(() => {
    usePipelineBuilder.setState({
      irNodes: [makeNode('A')],
      irEdges: [],
      selectedNodeId: null,
      isDirty: false,
    });
  });

  it('position 变更写回节点并标记 dirty', () => {
    const changes: NodeChange[] = [
      { id: 'A', type: 'position', position: { x: 100, y: 200 }, dragging: false },
    ];
    usePipelineBuilder.getState().onNodesChange(changes);
    const node = usePipelineBuilder.getState().irNodes[0];
    expect(node.position).toEqual({ x: 100, y: 200 });
    expect(usePipelineBuilder.getState().isDirty).toBe(true);
  });
});

describe('usePipelineBuilder.onNodesChange — remove 变更（Delete 键删除）', () => {
  beforeEach(() => {
    usePipelineBuilder.setState({
      irNodes: [makeNode('A'), makeNode('B', 'Aggregate'), makeNode('C', 'Sink')],
      irEdges: [
        { id: 'e_ab', source_id: 'A', target_id: 'B', source_port: 'default', target_port: 'default' },
        { id: 'e_bc', source_id: 'B', target_id: 'C', source_port: 'default', target_port: 'default' },
      ],
      selectedNodeId: 'B',
      isDirty: false,
      pastSnapshots: [],
      futureSnapshots: [],
    });
  });

  it('remove change 删除指定节点', () => {
    const changes: NodeChange[] = [{ id: 'B', type: 'remove' }];
    usePipelineBuilder.getState().onNodesChange(changes);
    const ids = usePipelineBuilder.getState().irNodes.map((n) => n.id);
    expect(ids).toEqual(['A', 'C']);
  });

  it('删除节点时级联删除关联边', () => {
    const changes: NodeChange[] = [{ id: 'B', type: 'remove' }];
    usePipelineBuilder.getState().onNodesChange(changes);
    const edges = usePipelineBuilder.getState().irEdges;
    expect(edges).toHaveLength(0); // e_ab 和 e_bc 都因 B 被删
  });

  it('删除当前选中节点时清空 selectedNodeId', () => {
    const changes: NodeChange[] = [{ id: 'B', type: 'remove' }];
    usePipelineBuilder.getState().onNodesChange(changes);
    expect(usePipelineBuilder.getState().selectedNodeId).toBeNull();
  });

  it('删除非选中节点不影响 selectedNodeId', () => {
    const changes: NodeChange[] = [{ id: 'A', type: 'remove' }];
    usePipelineBuilder.getState().onNodesChange(changes);
    expect(usePipelineBuilder.getState().selectedNodeId).toBe('B');
  });

  it('删除生成可撤销快照', () => {
    const changes: NodeChange[] = [{ id: 'B', type: 'remove' }];
    usePipelineBuilder.getState().onNodesChange(changes);
    const state = usePipelineBuilder.getState();
    expect(state.pastSnapshots).toHaveLength(1);
    expect(state.futureSnapshots).toHaveLength(0);
    // 快照内容是删除前的状态
    expect(state.pastSnapshots[0].irNodes.map((n) => n.id)).toEqual(['A', 'B', 'C']);
  });

  it('标记 isDirty', () => {
    const changes: NodeChange[] = [{ id: 'C', type: 'remove' }];
    usePipelineBuilder.getState().onNodesChange(changes);
    expect(usePipelineBuilder.getState().isDirty).toBe(true);
  });
});

describe('usePipelineBuilder.onEdgesChange — remove 变更（删边）', () => {
  beforeEach(() => {
    usePipelineBuilder.setState({
      irNodes: [makeNode('A'), makeNode('B', 'Aggregate'), makeNode('C', 'Sink')],
      irEdges: [
        { id: 'e_ab', source_id: 'A', target_id: 'B', source_port: 'default', target_port: 'default' },
        { id: 'e_bc', source_id: 'B', target_id: 'C', source_port: 'default', target_port: 'default' },
      ],
      selectedNodeId: 'B',
      isDirty: false,
      pastSnapshots: [],
      futureSnapshots: [],
    });
  });

  it('remove change 删除指定边', () => {
    const changes: EdgeChange[] = [{ id: 'e_ab', type: 'remove' }];
    usePipelineBuilder.getState().onEdgesChange(changes);
    const ids = usePipelineBuilder.getState().irEdges.map((e) => e.id);
    expect(ids).toEqual(['e_bc']);
  });

  it('删边不影响节点', () => {
    const changes: EdgeChange[] = [{ id: 'e_ab', type: 'remove' }];
    usePipelineBuilder.getState().onEdgesChange(changes);
    const nodeIds = usePipelineBuilder.getState().irNodes.map((n) => n.id);
    expect(nodeIds).toEqual(['A', 'B', 'C']);
  });

  it('删边生成可撤销快照', () => {
    const changes: EdgeChange[] = [{ id: 'e_ab', type: 'remove' }];
    usePipelineBuilder.getState().onEdgesChange(changes);
    const state = usePipelineBuilder.getState();
    expect(state.pastSnapshots).toHaveLength(1);
    expect(state.futureSnapshots).toHaveLength(0);
    // 快照内容是删除前的状态（含两条边）
    expect(state.pastSnapshots[0].irEdges.map((e) => e.id)).toEqual(['e_ab', 'e_bc']);
  });

  it('删边后 undo 可恢复', () => {
    usePipelineBuilder.getState().onEdgesChange([{ id: 'e_ab', type: 'remove' }]);
    expect(usePipelineBuilder.getState().irEdges).toHaveLength(1);
    usePipelineBuilder.getState().undo();
    expect(usePipelineBuilder.getState().irEdges).toHaveLength(2);
  });

  it('删边标记 isDirty', () => {
    usePipelineBuilder.getState().onEdgesChange([{ id: 'e_bc', type: 'remove' }]);
    expect(usePipelineBuilder.getState().isDirty).toBe(true);
  });

  it('非 remove 变更（select）不产生快照', () => {
    const changes: EdgeChange[] = [{ id: 'e_ab', type: 'select', selected: true }];
    usePipelineBuilder.getState().onEdgesChange(changes);
    expect(usePipelineBuilder.getState().pastSnapshots).toHaveLength(0);
  });

  it('select 变更回写 selected 到 irEdges（受控模式选中态保持）', () => {
    usePipelineBuilder.getState().onEdgesChange([
      { id: 'e_ab', type: 'select', selected: true } as EdgeChange,
    ]);
    const edges = usePipelineBuilder.getState().irEdges;
    const ab = edges.find((e) => e.id === 'e_ab');
    expect(ab?.selected).toBe(true);
    // 其他边不受影响
    const bc = edges.find((e) => e.id === 'e_bc');
    expect(bc?.selected).toBeFalsy();
  });

  it('select 变更不标记 isDirty（选中不是数据修改）', () => {
    usePipelineBuilder.setState({ isDirty: false });
    usePipelineBuilder.getState().onEdgesChange([
      { id: 'e_ab', type: 'select', selected: true } as EdgeChange,
    ]);
    expect(usePipelineBuilder.getState().isDirty).toBe(false);
  });
});

describe('usePipelineBuilder.onConnect — 连线', () => {
  beforeEach(() => {
    usePipelineBuilder.setState({
      irNodes: [makeNode('A', 'Source'), makeNode('B', 'Filter')],
      irEdges: [],
      selectedNodeId: null,
      isDirty: false,
      pastSnapshots: [],
      futureSnapshots: [],
    });
  });

  it('有效连接创建边', () => {
    usePipelineBuilder.getState().onConnect({
      source: 'A',
      target: 'B',
      sourceHandle: 'default',
      targetHandle: 'default',
    });
    const edges = usePipelineBuilder.getState().irEdges;
    expect(edges).toHaveLength(1);
    expect(edges[0].source_id).toBe('A');
    expect(edges[0].target_id).toBe('B');
    expect(edges[0].source_port).toBe('default');
    expect(edges[0].target_port).toBe('default');
    expect(usePipelineBuilder.getState().isDirty).toBe(true);
  });

  it('重复连接不创建边', () => {
    const conn = { source: 'A', target: 'B', sourceHandle: 'default', targetHandle: 'default' };
    usePipelineBuilder.getState().onConnect(conn);
    usePipelineBuilder.getState().onConnect(conn);
    expect(usePipelineBuilder.getState().irEdges).toHaveLength(1);
  });

  it('source 或 target 缺失时不创建边', () => {
    usePipelineBuilder.getState().onConnect({
      source: '', target: 'B', sourceHandle: 'default', targetHandle: 'default',
    });
    expect(usePipelineBuilder.getState().irEdges).toHaveLength(0);
  });

  it('连接生成可撤销快照', () => {
    usePipelineBuilder.getState().onConnect({
      source: 'A', target: 'B', sourceHandle: 'default', targetHandle: 'default',
    });
    const state = usePipelineBuilder.getState();
    expect(state.pastSnapshots).toHaveLength(1);
    expect(state.pastSnapshots[0].irEdges).toHaveLength(0); // 连线前的快照
  });

  it('同一输入端口只允许一条入边（端口级单输入约束）', () => {
    // B 的 default 输入端口已被 A 连接
    usePipelineBuilder.getState().onConnect({
      source: 'A', target: 'B', sourceHandle: 'default', targetHandle: 'default',
    });
    // 另一个 source C 再连 B 的同一 default 端口 → 应被拒绝
    usePipelineBuilder.setState({ irNodes: [makeNode('A', 'Source'), makeNode('B', 'Filter'), makeNode('C', 'Source')] });
    usePipelineBuilder.getState().onConnect({
      source: 'C', target: 'B', sourceHandle: 'default', targetHandle: 'default',
    });
    const edges = usePipelineBuilder.getState().irEdges;
    expect(edges).toHaveLength(1); // 仍是 A→B，C→B 被拒
    expect(edges[0].source_id).toBe('A');
  });
});

describe('usePipelineBuilder.setValidation — nodeSchemas 回填', () => {
  beforeEach(() => {
    usePipelineBuilder.setState({
      irNodes: [makeNode('A', 'Source'), makeNode('B', 'Filter')],
      irEdges: [{ id: 'e_ab', source_id: 'A', target_id: 'B', source_port: 'default', target_port: 'default' }],
      selectedNodeId: null,
      isDirty: false,
      nodeSchemas: {},
    });
  });

  it('setValidation 把 nodeSchemas 存到 state', () => {
    const nodeSchemas = {
      A: { fields: [{ name: 'id', data_type: 'STRING', nullable: false, description: '', primary_key: false }] },
    };
    usePipelineBuilder.getState().setValidation(true, [], nodeSchemas);
    expect(usePipelineBuilder.getState().nodeSchemas).toEqual(nodeSchemas);
  });

  it('setValidation 不传 nodeSchemas 时不改 nodeSchemas', () => {
    usePipelineBuilder.setState({ nodeSchemas: { A: { fields: [] } } });
    usePipelineBuilder.getState().setValidation(false, [{ node_id: 'A', valid: false, level: 'ERROR', message: 'err' }]);
    expect(usePipelineBuilder.getState().validationValid).toBe(false);
    expect(usePipelineBuilder.getState().validationErrors).toHaveLength(1);
    // nodeSchemas 保持不变（不被清空）
    expect(usePipelineBuilder.getState().nodeSchemas).toEqual({ A: { fields: [] } });
  });

  it('setValidation 回填不修改 irNodes（避免触发 validate effect 循环）', () => {
    const irNodesBefore = usePipelineBuilder.getState().irNodes;
    const nodeSchemas = {
      A: { fields: [{ name: 'id', data_type: 'STRING', nullable: false, description: '', primary_key: false }] },
    };
    usePipelineBuilder.getState().setValidation(true, [], nodeSchemas);
    // irNodes 引用不变（setValidation 只存 nodeSchemas，不回填 irNodes）
    expect(usePipelineBuilder.getState().irNodes).toBe(irNodesBefore);
  });
});

describe('usePipelineBuilder — 多选与批量操作', () => {
  beforeEach(() => {
    usePipelineBuilder.setState({
      irNodes: [makeNode('A'), makeNode('B', 'Aggregate'), makeNode('C', 'Sink')],
      irEdges: [],
      selectedNodeId: null,
      selectedNodeIds: [],
      isDirty: false,
      pastSnapshots: [],
      futureSnapshots: [],
    });
  });

  it('setSelectedNode 单选时清空多选集合', () => {
    usePipelineBuilder.setState({ selectedNodeIds: ['A', 'B'] });
    usePipelineBuilder.getState().setSelectedNode('C');
    expect(usePipelineBuilder.getState().selectedNodeId).toBe('C');
    expect(usePipelineBuilder.getState().selectedNodeIds).toEqual([]);
  });

  it('duplicateNodes 复制选中节点，生成副本并位移错开', () => {
    const newIds = usePipelineBuilder.getState().duplicateNodes(['A', 'B']);
    expect(newIds).toHaveLength(2);
    const nodes = usePipelineBuilder.getState().irNodes;
    expect(nodes).toHaveLength(5); // 原 3 + 副本 2
    // 副本 label 带「副本」后缀
    const copies = nodes.filter((n) => newIds.includes(n.id));
    expect(copies.every((n) => n.label.includes('副本'))).toBe(true);
    // 副本位移错开（原 A 在 0,0，副本在 40,40）
    expect(copies[0].position).toEqual({ x: 40, y: 40 });
    // 标脏
    expect(usePipelineBuilder.getState().isDirty).toBe(true);
    // 选中集合变为新副本
    expect(usePipelineBuilder.getState().selectedNodeIds).toEqual(newIds);
  });

  it('duplicateNodes 对不存在的 id 返回空', () => {
    const newIds = usePipelineBuilder.getState().duplicateNodes(['NOT_EXIST']);
    expect(newIds).toEqual([]);
    expect(usePipelineBuilder.getState().irNodes).toHaveLength(3);
  });

  it('updateNodePositions 批量更新位置并标脏', () => {
    usePipelineBuilder.getState().updateNodePositions([
      { id: 'A', position: { x: 100, y: 100 } },
      { id: 'B', position: { x: 200, y: 200 } },
    ]);
    const nodes = usePipelineBuilder.getState().irNodes;
    const a = nodes.find((n) => n.id === 'A');
    const b = nodes.find((n) => n.id === 'B');
    const c = nodes.find((n) => n.id === 'C');
    expect(a?.position).toEqual({ x: 100, y: 100 });
    expect(b?.position).toEqual({ x: 200, y: 200 });
    expect(c?.position).toEqual({ x: 0, y: 0 }); // 未更新
    expect(usePipelineBuilder.getState().isDirty).toBe(true);
  });

  it('updateNodePositions 空数组不标脏', () => {
    usePipelineBuilder.getState().updateNodePositions([]);
    expect(usePipelineBuilder.getState().isDirty).toBe(false);
  });

  it('removeNode 清理多选集合中的已删节点', () => {
    usePipelineBuilder.setState({ selectedNodeIds: ['A', 'B', 'C'] });
    usePipelineBuilder.getState().removeNode('B');
    expect(usePipelineBuilder.getState().selectedNodeIds).toEqual(['A', 'C']);
  });
});
