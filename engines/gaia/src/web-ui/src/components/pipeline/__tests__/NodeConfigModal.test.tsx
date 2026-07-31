/**
 * NodeConfigModal 单元测试。
 *
 * 验证：
 * - node=null 时弹窗关闭（不渲染配置表单）
 * - node 存在时渲染节点配置面板（含节点名称）
 * - onClose 回调被调用（点击关闭按钮）
 */
import { describe, it, expect, vi } from 'vitest';
import { render, screen, fireEvent } from '@testing-library/react';
import { NodeConfigModal } from '../NodeConfigModal';
import type { IRNode } from '../../../types/pipeline';

function makeNode(overrides: Partial<IRNode> = {}): IRNode {
  return {
    id: 'n1',
    type: 'Transform',
    operator_type: 'Filter',
    label: '我的过滤节点',
    description: '',
    input_schemas: [],
    output_schema: null,
    config: { filter_conditions: [{ column: 'status', operator: 'eq', value: 'active' }] },
    position: { x: 0, y: 0 },
    ...overrides,
  };
}

describe('NodeConfigModal', () => {
  it('node=null 时不渲染配置表单', () => {
    const { container } = render(
      <NodeConfigModal
        node={null}
        datasets={[]}
        nodeSchemas={{}}
        irEdges={[]}
        onChange={() => {}}
        onClose={() => {}}
      />,
    );
    // Modal 在 open=false 时 return null
    expect(container).toBeEmptyDOMElement();
  });

  it('node 存在时渲染节点名称', () => {
    render(
      <NodeConfigModal
        node={makeNode()}
        datasets={[]}
        nodeSchemas={{}}
        irEdges={[]}
        onChange={() => {}}
        onClose={() => {}}
      />,
    );
    expect(screen.getByText('我的过滤节点')).toBeInTheDocument();
  });

  it('点击关闭按钮调用 onClose', () => {
    const onClose = vi.fn();
    render(
      <NodeConfigModal
        node={makeNode()}
        datasets={[]}
        nodeSchemas={{}}
        irEdges={[]}
        onChange={() => {}}
        onClose={onClose}
      />,
    );
    // NodeConfigModal 的关闭按钮（✕）。有两个：Modal 头部的和 NodeConfigPanel 的，点任一都应关闭
    const closeBtns = screen.getAllByRole('button', { name: '✕' });
    fireEvent.click(closeBtns[0]);
    expect(onClose).toHaveBeenCalledOnce();
  });
});
