/**
 * LayersPanel 组件测试（图层样式面板）。
 */
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent } from '@testing-library/react';
import { useGraphExplore } from '../../hooks/useGraphExplore';
import { LayersPanel } from '../LayersPanel';

vi.mock('../../api/graph', () => ({
  queryDataFrame: vi.fn(),
  traverseLink: vi.fn(),
}));

function renderWithHook() {
  const captured: { explore: ReturnType<typeof useGraphExplore> | null } = { explore: null };
  function Wrapper() {
    const explore = useGraphExplore('ONT');
    captured.explore = explore;
    return <LayersPanel explore={explore} />;
  }
  render(<Wrapper />);
  return { getExplore: () => captured.explore! };
}

describe('LayersPanel', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    localStorage.clear();
  });

  it('无节点时显示提示', () => {
    renderWithHook();
    expect(screen.getByText('加载对象后可配置图层样式')).toBeInTheDocument();
  });

  it('默认着色按类型、大小固定', () => {
    renderWithHook();
    const typeRadio = screen.getByLabelText('按类型');
    expect(typeRadio).toBeChecked();
    const fixedRadio = screen.getByLabelText('固定');
    expect(fixedRadio).toBeChecked();
  });

  it('重置按钮恢复默认样式', () => {
    const { getExplore } = renderWithHook();
    // 先改成按度数
    fireEvent.click(screen.getByLabelText('按度数'));
    expect(getExplore().layerStyle.sizeBy).toBe('degree');
    // 重置
    fireEvent.click(screen.getByText('重置样式'));
    expect(getExplore().layerStyle.sizeBy).toBe('fixed');
    expect(getExplore().layerStyle.colorBy).toBe('type');
  });
});
