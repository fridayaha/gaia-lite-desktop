/**
 * DeletableEdge 单元测试
 *
 * 验证防误删设计：
 * - 未选中时不渲染删除按钮（避免 hover 误触）
 * - 选中时渲染删除按钮
 * - 点击按钮调用 setEdges 删除对应边
 */
import { describe, it, expect, vi, beforeEach } from 'vitest';
import React from 'react';
import { render, screen, fireEvent } from '@testing-library/react';
import { ReactFlowProvider } from '@xyflow/react';
import { DeletableEdge } from '../DeletableEdge';
import type { EdgeProps } from '@xyflow/react';

// 顶层 mock useReactFlow，返回稳定的 setEdges mock
// 同时 mock EdgeLabelRenderer 为透传 div（避免 portal 需要真实 ReactFlow DOM）
const setEdgesMock = vi.fn();
vi.mock('@xyflow/react', async () => {
  const actual = await vi.importActual<typeof import('@xyflow/react')>('@xyflow/react');
  return {
    ...actual,
    useReactFlow: () => ({ setEdges: setEdgesMock }),
    EdgeLabelRenderer: ({ children }: { children: React.ReactNode }) =>
      React.createElement('div', null, children),
  };
});

// 构造最小 EdgeProps
function makeProps(overrides: Partial<EdgeProps> = {}): EdgeProps {
  return {
    id: 'edge-1',
    source: 'src',
    target: 'tgt',
    sourceX: 0,
    sourceY: 0,
    targetX: 100,
    targetY: 0,
    sourcePosition: 'right' as any,
    targetPosition: 'left' as any,
    selected: false,
    ...overrides,
  } as EdgeProps;
}

describe('DeletableEdge — 防误删设计', () => {
  beforeEach(() => {
    setEdgesMock.mockReset();
  });

  it('未选中时不渲染删除按钮（防误删核心）', () => {
    render(
      <ReactFlowProvider>
        <DeletableEdge {...makeProps({ selected: false })} />
      </ReactFlowProvider>,
    );
    expect(screen.queryByLabelText('删除连线')).not.toBeInTheDocument();
  });

  it('选中时渲染删除按钮', () => {
    render(
      <ReactFlowProvider>
        <DeletableEdge {...makeProps({ selected: true })} />
      </ReactFlowProvider>,
    );
    expect(screen.getByLabelText('删除连线')).toBeInTheDocument();
  });

  it('点击删除按钮调用 setEdges 过滤掉当前边', () => {
    render(
      <ReactFlowProvider>
        <DeletableEdge {...makeProps({ selected: true })} />
      </ReactFlowProvider>,
    );
    const btn = screen.getByLabelText('删除连线');
    fireEvent.click(btn);
    expect(setEdgesMock).toHaveBeenCalledTimes(1);
    // setEdges 接收 updater 函数，过滤掉 id='edge-1'
    const updater = setEdgesMock.mock.calls[0][0];
    const result = updater([{ id: 'edge-1' }, { id: 'edge-2' }] as any);
    expect(result).toHaveLength(1);
    expect(result[0].id).toBe('edge-2');
  });

  it('删除按钮点击 stopPropagation 不触发画布事件', () => {
    render(
      <ReactFlowProvider>
        <DeletableEdge {...makeProps({ selected: true })} />
      </ReactFlowProvider>,
    );
    const btn = screen.getByLabelText('删除连线');
    // 模拟点击带 stopPropagation
    const stopSpy = vi.fn();
    btn.addEventListener('click', (e) => stopSpy(e.stopPropagation));
    fireEvent.click(btn);
    // setEdges 被调用说明删除触发
    expect(setEdgesMock).toHaveBeenCalled();
  });
});
