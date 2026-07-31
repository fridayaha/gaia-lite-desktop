import { describe, it, expect, vi, beforeEach } from 'vitest';
import {
  getMe,
  checkAccess,
  listAccessRequests,
  createAccessRequest,
  listAuditLogs,
  listMarkingCategories,
  listMarkings,
} from '../permission';

// Mock the shared request helper.
vi.mock('../client', () => ({
  request: vi.fn(),
  ApiError: class ApiError extends Error {
    status: number;
    constructor(message: string, status: number) {
      super(message);
      this.status = status;
    }
  },
}));

import { request } from '../client';
const mockedRequest = vi.mocked(request);

beforeEach(() => {
  mockedRequest.mockReset();
});

describe('permission API client', () => {
  it('getMe calls /auth/me', async () => {
    const me = { id: 'alice', is_anonymous: false, display_name: 'Alice',
      principal_type: 'USER' as const, attributes: {}, groups: [], roles: [],
      markings: [], home_organization: null };
    mockedRequest.mockResolvedValue(me as any);
    const result = await getMe();
    expect(mockedRequest).toHaveBeenCalledWith('/auth/me');
    expect(result.id).toBe('alice');
  });

  it('checkAccess builds query params', async () => {
    mockedRequest.mockResolvedValue({ decision: 'DENY' } as any);
    await checkAccess('OBJECT_TYPE', 'Invoice', 'object:view');
    expect(mockedRequest).toHaveBeenCalledWith(
      expect.stringContaining('/authz/check?resource_type=OBJECT_TYPE&resource_id=Invoice&action=object%3Aview'),
    );
  });

  it('listAccessRequests with pendingOnly', async () => {
    mockedRequest.mockResolvedValue([] as any);
    await listAccessRequests(true);
    expect(mockedRequest).toHaveBeenCalledWith('/authz/access-requests?pending_only=true');
  });

  it('createAccessRequest POSTs body', async () => {
    mockedRequest.mockResolvedValue({ id: 'r1', status: 'PENDING' } as any);
    await createAccessRequest({
      request_type: 'ROLE_ASSIGNMENT',
      requested_item: 'VIEWER',
      justification: 'test',
    });
    expect(mockedRequest).toHaveBeenCalledWith('/authz/access-requests', {
      method: 'POST',
      body: expect.stringContaining('"requested_item":"VIEWER"'),
    });
  });

  it('listAuditLogs builds optional params', async () => {
    mockedRequest.mockResolvedValue([] as any);
    await listAuditLogs({ result: 'DENY', limit: 50 });
    const arg = mockedRequest.mock.calls[0][0] as string;
    expect(arg).toContain('/audit-logs?');
    expect(arg).toContain('result=DENY');
    expect(arg).toContain('limit=50');
  });

  it('listMarkings with categoryId', async () => {
    mockedRequest.mockResolvedValue([] as any);
    await listMarkings('cat-1');
    expect(mockedRequest).toHaveBeenCalledWith('/markings?category_id=cat-1');
  });

  it('listMarkings without categoryId', async () => {
    mockedRequest.mockResolvedValue([] as any);
    await listMarkings();
    expect(mockedRequest).toHaveBeenCalledWith('/markings');
  });

  it('listMarkingCategories', async () => {
    mockedRequest.mockResolvedValue([] as any);
    await listMarkingCategories();
    expect(mockedRequest).toHaveBeenCalledWith('/marking-categories');
  });
});
