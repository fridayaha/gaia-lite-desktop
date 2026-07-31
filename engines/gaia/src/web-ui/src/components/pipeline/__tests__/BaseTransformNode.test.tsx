/**
 * BaseTransformNode 单元测试 — 验证配置摘要区在节点卡片上渲染。
 */
import { describe, it, expect } from 'vitest';
import { render, screen } from '@testing-library/react';
import { ReactFlowProvider } from '@xyflow/react';
import { BaseTransformNode } from '../BaseTransformNode';
import type { NodeProps, Node } from '@xyflow/react';
import type { PipelineNodeData } from '../../../types/pipeline';

function makeProps(
  dataOverrides: Partial<PipelineNodeData> = {},
): NodeProps<Node<PipelineNodeData>> {
  return {
    id: 'n1',
    type: 'Filter',
    data: {
      irNodeId: 'n1',
      label: '过滤节点',
      nodeType: 'Transform',
      operatorType: 'Filter',
      validationStatus: 'valid',
      validationMessages: [],
      outputSchemaSummary: '3 字段',
      configSummary: [{ text: 'status = "active"', primary: true }],
      isRunning: false,
      configurable: true,
      ...dataOverrides,
    },
    selected: false,
    dragging: false,
    sourcePosition: undefined,
    targetPosition: undefined,
    zIndex: 0,
    dragHandle: undefined,
    isConnectable: true,
    positionAbsoluteX: 0,
    positionAbsoluteY: 0,
    width: 200,
    height: 100,
    // NodeProps 还可能有其他可选字段，用索引签名兜底
  } as unknown as NodeProps<Node<PipelineNodeData>>;
}

// BaseTransformNode 内部用 <Handle>，需 ReactFlowProvider 提供 store 上下文
function renderWithProvider(ui: React.ReactNode) {
  return render(<ReactFlowProvider>{ui}</ReactFlowProvider>);
}

describe('BaseTransformNode', () => {
  it('渲染节点名称', () => {
    renderWithProvider(<BaseTransformNode {...makeProps()} />);
    expect(screen.getByText('过滤节点')).toBeInTheDocument();
  });

  it('渲染配置摘要（核心配置可视化）', () => {
    renderWithProvider(<BaseTransformNode {...makeProps()} />);
    expect(screen.getByText('status = "active"')).toBeInTheDocument();
  });

  it('configSummary 为空时不渲染摘要区', () => {
    const { container } = renderWithProvider(
      <BaseTransformNode {...makeProps({ configSummary: [] })} />,
    );
    // 摘要区有 font-mono 类；空时不出现。检查没有额外的 mono 文本行
    expect(container.querySelector('.font-mono')).toBeNull();
  });

  it('多行摘要都渲染', () => {
    renderWithProvider(
      <BaseTransformNode
        {...makeProps({
          configSummary: [
            { text: '主条件', primary: true },
            { text: '共 3 个条件' },
          ],
        })}
      />,
    );
    expect(screen.getByText('主条件')).toBeInTheDocument();
    expect(screen.getByText('共 3 个条件')).toBeInTheDocument();
  });
});
