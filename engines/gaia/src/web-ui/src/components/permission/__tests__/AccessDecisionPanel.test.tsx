import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import { AccessDecisionPanel } from '../AccessDecisionPanel';
import * as permApi from '../../../api/permission';
import { ApiError } from '../../../api/client';
import type { CheckAccessResult } from '../../../api/permission';

vi.mock('../../../api/client', () => ({
  ApiError: class ApiError extends Error {
    status: number;
    constructor(message: string, status: number) {
      super(message);
      this.status = status;
    }
  },
}));

const allowResult: CheckAccessResult = {
  principal_id: 'u1', resource_type: 'DATASOURCE', resource_id: 'ds-1',
  action: 'datasource:view', decision: 'ALLOW', layer: null, reason: '',
  layers: { identity: true, org: true, space: true, project: true, marking: true, row: true },
  missing: [], provenance: ['Group → VIEWER'],
} as any;

const denyResult: CheckAccessResult = {
  principal_id: 'u1', resource_type: 'DATASOURCE', resource_id: 'ds-1',
  action: 'datasource:delete', decision: 'DENY', layer: 'PROJECT', reason: '需要 Owner 角色',
  layers: { identity: true, org: true, space: true, project: false, marking: true, row: true },
  missing: ['datasource:delete'], provenance: [],
} as any;

describe('AccessDecisionPanel', () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it('renders allow badge and provenance when allowed', () => {
    render(<AccessDecisionPanel result={allowResult} />);
    expect(screen.getByText('✓ 允许')).toBeInTheDocument();
    expect(screen.getByText('Group → VIEWER')).toBeInTheDocument();
    // No request CTA when allowed.
    expect(screen.queryByText('申请权限')).not.toBeInTheDocument();
  });

  it('renders deny badge, layer stepper, and missing permissions when denied', () => {
    render(<AccessDecisionPanel result={denyResult} />);
    expect(screen.getByText('✗ 拒绝')).toBeInTheDocument();
    expect(screen.getByText(/需要 Owner 角色/)).toBeInTheDocument();
    expect(screen.getByText('datasource:delete')).toBeInTheDocument();
    expect(screen.getByText('申请权限')).toBeInTheDocument();
  });

  it('shows all six layers in the stepper', () => {
    render(<AccessDecisionPanel result={denyResult} />);
    expect(screen.getByText('身份认证')).toBeInTheDocument();
    expect(screen.getByText('Organization')).toBeInTheDocument();
    expect(screen.getByText('Space 准入')).toBeInTheDocument();
    expect(screen.getByText('Project RBAC')).toBeInTheDocument();
    expect(screen.getByText('Marking MAC')).toBeInTheDocument();
    expect(screen.getByText('行/列级')).toBeInTheDocument();
  });

  it('submits a JIT access request on click', async () => {
    const spy = vi.spyOn(permApi, 'createAccessRequest').mockResolvedValue({
      id: 'req12345678', requester_id: 'u1', request_type: 'ROLE_ASSIGNMENT',
      requested_item: 'VIEWER', scope_type: 'PROJECT', scope_id: null,
      justification: '', status: 'PENDING', reviewer_id: null, review_comment: '',
      reviewed_at: null, expires_at: null, created_at: '', updated_at: '',
    } as any);
    const onRequested = vi.fn();

    render(<AccessDecisionPanel result={denyResult} onRequested={onRequested} />);
    fireEvent.click(screen.getByText('申请权限'));

    await waitFor(() => {
      expect(spy).toHaveBeenCalledTimes(1);
      expect(screen.getByText(/申请已提交/)).toBeInTheDocument();
      expect(onRequested).toHaveBeenCalled();
    });
  });

  it('shows error message when JIT request fails', async () => {
    vi.spyOn(permApi, 'createAccessRequest').mockRejectedValue(
      new ApiError('forbidden', 403),
    );

    render(<AccessDecisionPanel result={denyResult} />);
    fireEvent.click(screen.getByText('申请权限'));

    await waitFor(() => {
      expect(screen.getByText(/申请失败/)).toBeInTheDocument();
    });
  });
});
