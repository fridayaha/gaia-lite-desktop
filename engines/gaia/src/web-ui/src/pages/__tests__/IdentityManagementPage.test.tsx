/**
 * Tests for IdentityManagementPage (ADR-016 §8.4).
 *
 * Verifies the two-tab structure (groups/users), group list rendering,
 * and the permission gate on create buttons.
 */
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, waitFor, fireEvent } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { IdentityManagementPage } from '../IdentityManagementPage';

// Mock the permission API
vi.mock('../../api/permission', () => ({
  listUsers: vi.fn().mockResolvedValue([
    { id: 'u1', email: 'alice@test.local', subject: 'alice-sub', attributes: { dept: 'eng' } },
  ]),
  createUser: vi.fn(),
  listGroups: vi.fn().mockResolvedValue([
    { id: 'g1', name: 'editors', description: 'Editor group', organization_id: 'org1', parent_group_id: null },
  ]),
  createGroup: vi.fn(),
  listGroupMembers: vi.fn().mockResolvedValue([]),
  addGroupMember: vi.fn(),
  removeGroupMember: vi.fn(),
  listUserGroups: vi.fn().mockResolvedValue([]),
  listOrganizations: vi.fn().mockResolvedValue([
    { id: 'org1', api_name: 'org-default', display_name: 'Default Org' },
  ]),
  listRoleAssignments: vi.fn().mockResolvedValue([]),
  deleteRoleAssignment: vi.fn(),
  listProjects: vi.fn().mockResolvedValue([]),
  ApiError: class ApiError extends Error {
    status: number;
    constructor(m: string, status: number) { super(m); this.status = status; }
  },
}));

// Mock useAllowedActions — admin has role:manage
vi.mock('../../hooks/useAllowedActions', () => ({
  useAllowedActions: vi.fn(() => ({
    decisions: { '*': { allowedActions: ['role:manage'], disabledReasons: {} } },
    loading: false,
    error: null,
    isAllowed: () => true,
    disabledReason: () => '',
  })),
}));

// Mock PermissionGate to always render children (admin has permission)
vi.mock('../../components/permission', () => ({
  PermissionGate: ({ children }: { children: React.ReactNode }) => <>{children}</>,
}));

function renderPage() {
  return render(
    <MemoryRouter>
      <IdentityManagementPage />
    </MemoryRouter>
  );
}

describe('IdentityManagementPage', () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it('renders the page title and description', async () => {
    renderPage();
    expect(screen.getByText('身份管理')).toBeInTheDocument();
    expect(screen.getByText(/组授权铁律/)).toBeInTheDocument();
  });

  it('shows groups tab by default with group list', async () => {
    renderPage();
    await waitFor(() => {
      expect(screen.getByText('editors')).toBeInTheDocument();
    });
  });

  it('switches to users tab', async () => {
    renderPage();
    await waitFor(() => {
      expect(screen.getByText('editors')).toBeInTheDocument();
    });
    fireEvent.click(screen.getByText('🧑 用户'));
    await waitFor(() => {
      expect(screen.getByText('alice@test.local')).toBeInTheDocument();
    });
  });

  it('shows create group button (permission gate passed)', async () => {
    renderPage();
    await waitFor(() => {
      expect(screen.getByText('+ 新建组')).toBeInTheDocument();
    });
  });
});
