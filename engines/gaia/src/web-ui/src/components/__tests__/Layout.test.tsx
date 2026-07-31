import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, waitFor, fireEvent, act } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { AppLayout } from '../Layout';
import * as permApi from '../../api/permission';

vi.mock('../../api/permission', () => ({
  getDeploymentInfo: vi.fn(),
}));

// ThemeToggle depends on window.matchMedia which jsdom doesn't implement.
vi.mock('../ThemeToggle', () => ({
  ThemeToggle: () => <div data-testid="theme-toggle-mock" />,
}));

function renderLayout() {
  return render(
    <MemoryRouter initialEntries={['/']}>
      <AppLayout />
    </MemoryRouter>,
  );
}

describe('AppLayout information architecture (design §8.1)', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    localStorage.clear();
  });

  it('shows business navigation in primary rail (ontology/data/ops)', () => {
    vi.mocked(permApi.getDeploymentInfo).mockResolvedValue({ is_multi_tenant: false });
    renderLayout();
    // Primary business items visible.
    expect(screen.getByLabelText('本体构建')).toBeInTheDocument();
    expect(screen.getByLabelText('数据集成')).toBeInTheDocument();
    expect(screen.getByLabelText('运营看板')).toBeInTheDocument();
  });

  it('groups permission management under Settings (not primary)', async () => {
    vi.mocked(permApi.getDeploymentInfo).mockResolvedValue({ is_multi_tenant: false });
    renderLayout();
    // Settings group present (collapsed by default — expand to verify children).
    expect(screen.getByLabelText('设置')).toBeInTheDocument();
    // The old top-level "权限管理" label must NOT exist.
    expect(screen.queryByLabelText('权限管理')).not.toBeInTheDocument();
  });

  it('hides three-tier container management in single-tenant mode', async () => {
    vi.mocked(permApi.getDeploymentInfo).mockResolvedValue({ is_multi_tenant: false });
    renderLayout();
    await waitFor(() => {
      fireEvent.click(screen.getByLabelText('设置'));
    });
    // 组织管理 should NOT appear in single-tenant mode (after expanding settings).
    expect(screen.queryByLabelText('组织管理')).not.toBeInTheDocument();
  });

  it('shows three-tier container management in multi-tenant mode', async () => {
    vi.mocked(permApi.getDeploymentInfo).mockResolvedValue({ is_multi_tenant: true });
    renderLayout();
    // Wait for multi-tenant signal to load (railItems updates asynchronously).
    await waitFor(() => {
      expect(screen.getByLabelText('设置')).toBeInTheDocument();
    });
    // Expand Settings group (default collapsed).
    act(() => {
      fireEvent.click(screen.getByLabelText('设置'));
    });
    await waitFor(() => {
      expect(screen.getByLabelText('组织管理')).toBeInTheDocument();
    });
  });
});
