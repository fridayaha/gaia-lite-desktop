/**
 * PathFinder 组件测试（路径推理面板）。
 */
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import { PathFinder } from '../PathFinder';

vi.mock('../../api/graph', () => ({
  findPaths: vi.fn(),
}));

import { findPaths } from '../../api/graph';
const mockFind = findPaths as ReturnType<typeof vi.fn>;

describe('PathFinder', () => {
  beforeEach(() => vi.clearAllMocks());

  it('未输入源/目标时不调 API', () => {
    render(<PathFinder ontology="ONT" selectedVid="" nodeVids={['S001', 'S002']} />);
    fireEvent.click(screen.getByText('查找路径'));
    expect(mockFind).not.toHaveBeenCalled();
  });

  it('找到路径时显示 rid 序列', async () => {
    mockFind.mockResolvedValue({
      source: 'S001', target: 'S002',
      paths: [['S001', 'O1', 'S002']], count: 1,
    });
    render(<PathFinder ontology="ONT" selectedVid="S001" nodeVids={['S001', 'S002', 'O1']} />);

    // 源已预填 S001，选目标 S002
    const selects = screen.getAllByRole('combobox');
    fireEvent.change(selects[1], { target: { value: 'S002' } });
    fireEvent.click(screen.getByText('查找路径'));

    await waitFor(() => {
      expect(screen.getByText('找到 1 条最短路径')).toBeInTheDocument();
    });
    // 路径展示在带 bg-slate-50 的容器里
    const pathContainer = screen.getByText('找到 1 条最短路径').nextElementSibling;
    expect(pathContainer?.textContent).toContain('O1');
    expect(pathContainer?.textContent).toContain('S001');
    expect(pathContainer?.textContent).toContain('S002');
  });

  it('API 失败时显示错误', async () => {
    mockFind.mockRejectedValue(new Error('Neo4j 不可用'));
    render(<PathFinder ontology="ONT" selectedVid="S001" nodeVids={['S001', 'S002']} />);

    const selects = screen.getAllByRole('combobox');
    fireEvent.change(selects[1], { target: { value: 'S002' } });
    fireEvent.click(screen.getByText('查找路径'));

    await waitFor(() => {
      expect(screen.getByText(/Neo4j 不可用/)).toBeInTheDocument();
    });
  });

  it('无连接时显示 0 条路径', async () => {
    mockFind.mockResolvedValue({ source: 'S001', target: 'S002', paths: [], count: 0 });
    render(<PathFinder ontology="ONT" selectedVid="S001" nodeVids={['S001', 'S002']} />);

    const selects = screen.getAllByRole('combobox');
    fireEvent.change(selects[1], { target: { value: 'S002' } });
    fireEvent.click(screen.getByText('查找路径'));

    await waitFor(() => {
      expect(screen.queryByText('找到 1 条最短路径')).not.toBeInTheDocument();
    });
  });
});
