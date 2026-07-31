import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { PermissionedRoute } from '../PermissionedRoute';
import { ForbiddenPage } from '../ForbiddenPage';
import * as permApi from '../../../api/permission';
import { ApiError } from '../../../api/client';

vi.mock('react-router-dom', async () => {
  const actual = await vi.importActual('react-router-dom');
  return { ...actual };
});

function renderWithRouter(ui: React.ReactNode) {
  return render(<MemoryRouter>{ui}</MemoryRouter>);
}

describe('PermissionedRoute', () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it('renders children when access is allowed', async () => {
    vi.spyOn(permApi, 'checkAccess').mockResolvedValue({
      principal_id: 'u1', resource_type: 'MARKING', resource_id: '*',
      action: 'marking:manage', decision: 'ALLOW', layer: null, reason: '',
      layers: {}, missing: [], provenance: [],
    } as any);

    renderWithRouter(
      <PermissionedRoute resourceType="MARKING" resourceId="*" action="marking:manage">
        <div>管理面板</div>
      </PermissionedRoute>,
    );

    await waitFor(() => {
      expect(screen.getByText('管理面板')).toBeInTheDocument();
    });
  });

  it('renders fallback when access is denied', async () => {
    vi.spyOn(permApi, 'checkAccess').mockResolvedValue({
      principal_id: 'u1', resource_type: 'MARKING', resource_id: '*',
      action: 'marking:manage', decision: 'DENY', layer: 'PROJECT', reason: '需要 MARKING_ADMIN',
      layers: {}, missing: ['marking:manage'], provenance: [],
    } as any);

    renderWithRouter(
      <PermissionedRoute
        resourceType="MARKING"
        resourceId="*"
        action="marking:manage"
        fallback={<ForbiddenPage action="marking:manage" resourceType="MARKING" />}
      >
        <div>管理面板</div>
      </PermissionedRoute>,
    );

    await waitFor(() => {
      expect(screen.queryByText('管理面板')).not.toBeInTheDocument();
      expect(screen.getByText('无权访问')).toBeInTheDocument();
    });
  });

  it('shows error state on non-403 failure (not silent)', async () => {
    vi.spyOn(permApi, 'checkAccess').mockRejectedValue(new Error('network down'));

    renderWithRouter(
      <PermissionedRoute resourceType="MARKING" resourceId="*" action="marking:manage">
        <div>管理面板</div>
      </PermissionedRoute>,
    );

    await waitFor(() => {
      expect(screen.queryByText('管理面板')).not.toBeInTheDocument();
      expect(screen.getByText(/权限校验失败/)).toBeInTheDocument();
    });
  });

  it('treats 403 as denied (not error)', async () => {
    vi.spyOn(permApi, 'checkAccess').mockRejectedValue(
      new ApiError('forbidden', 403),
    );

    renderWithRouter(
      <PermissionedRoute
        resourceType="AUDIT"
        resourceId="*"
        action="audit:read"
        fallback={<div>被拒</div>}
      >
        <div>审计日志</div>
      </PermissionedRoute>,
    );

    await waitFor(() => {
      expect(screen.getByText('被拒')).toBeInTheDocument();
      expect(screen.queryByText('审计日志')).not.toBeInTheDocument();
    });
  });
});

describe('ForbiddenPage', () => {
  it('renders denial message with action and resource context', () => {
    renderWithRouter(<ForbiddenPage action="marking:manage" resourceType="MARKING" />);
    expect(screen.getByText('无权访问')).toBeInTheDocument();
    expect(screen.getByText(/资源: MARKING/)).toBeInTheDocument();
    expect(screen.getByText(/操作: marking:manage/)).toBeInTheDocument();
  });

  it('offers return-home and request-access actions', () => {
    renderWithRouter(<ForbiddenPage />);
    expect(screen.getByText('返回首页')).toBeInTheDocument();
    expect(screen.getByText('申请权限')).toBeInTheDocument();
  });
});
