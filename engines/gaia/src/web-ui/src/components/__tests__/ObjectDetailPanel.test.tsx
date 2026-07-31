import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import { ObjectDetailPanel } from '../ObjectDetailPanel';
import * as clientApi from '../../api/client';

vi.mock('../../api/client', () => ({
  getObjectType: vi.fn(),
  listLinkTypes: vi.fn(),
  listActionTypes: vi.fn(),
  listDatasets: vi.fn(),
}));

vi.mock('../../api/permission', () => ({
  useAllowedActions: vi.fn(() => ({ decisions: {}, loading: false })),
  checkAccess: vi.fn(),
  createRoleAssignment: vi.fn(),
  listRoleAssignments: vi.fn(),
  deleteRoleAssignment: vi.fn(),
  assignMarking: vi.fn(),
  revokeMarking: vi.fn(),
  listMarkings: vi.fn(),
}));

vi.mock('../../hooks/useAllowedActions', () => ({
  useAllowedActions: vi.fn(() => ({ decisions: {}, loading: false })),
}));

const _ot = {
  id: 'ot1', api_name: 'Invoice', display_name: 'Invoice',
  storage_type: 'MANAGED', ontology_id: 'ont1',
};
const _detail = {
  id: 'ot1', api_name: 'Invoice', display_name: 'Invoice', description: 'An invoice',
  primary_key: 'id', title_property: 'id', storage_type: 'MANAGED',
  properties: [], ontology_id: 'ont1',
};

describe('ObjectDetailPanel tab structure (design §8.4)', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    vi.mocked(clientApi.getObjectType).mockResolvedValue(_detail as any);
    vi.mocked(clientApi.listLinkTypes).mockResolvedValue([]);
    vi.mocked(clientApi.listActionTypes).mockResolvedValue([]);
    vi.mocked(clientApi.listDatasets).mockResolvedValue([]);
  });

  it('renders three tabs: overview / definition / access', async () => {
    render(
      <ObjectDetailPanel
        ontologyName="finance"
        objectType={_ot as any}
        onEdit={() => {}}
        onDelete={() => {}}
        onClose={() => {}}
      />,
    );
    await waitFor(() => screen.getByText('概览'));
    expect(screen.getByText('概览')).toBeInTheDocument();
    expect(screen.getByText('定义')).toBeInTheDocument();
    expect(screen.getByText('访问控制')).toBeInTheDocument();
  });

  it('defaults to definition tab showing properties/relationships/actions', async () => {
    render(
      <ObjectDetailPanel
        ontologyName="finance"
        objectType={_ot as any}
        onEdit={() => {}}
        onDelete={() => {}}
        onClose={() => {}}
      />,
    );
    await waitFor(() => screen.getByText('属性 (0)'));
    expect(screen.getByText('属性 (0)')).toBeInTheDocument();
    expect(screen.getByText('关系 (0)')).toBeInTheDocument();
    expect(screen.getByText('动作 (0)')).toBeInTheDocument();
  });

  it('overview tab shows metadata + project info', async () => {
    render(
      <ObjectDetailPanel
        ontologyName="finance"
        objectType={_ot as any}
        onEdit={() => {}}
        onDelete={() => {}}
        onClose={() => {}}
      />,
    );
    await waitFor(() => screen.getByText('概览'));
    fireEvent.click(screen.getByText('概览'));
    expect(screen.getByText('API 名称')).toBeInTheDocument();
    expect(screen.getByText('所属 Project')).toBeInTheDocument();
    // TODO: 「继承自本体的 Space」显示待 Option A 迁移后补全（space 归属链路）
  });

  it('access tab shows permission decisions + grant + marking sections', async () => {
    render(
      <ObjectDetailPanel
        ontologyName="finance"
        objectType={_ot as any}
        onEdit={() => {}}
        onDelete={() => {}}
        onClose={() => {}}
      />,
    );
    await waitFor(() => screen.getByText('访问控制'));
    fireEvent.click(screen.getByText('访问控制'));
    expect(screen.getByText('当前用户权限')).toBeInTheDocument();
    expect(screen.getByText('角色授予')).toBeInTheDocument();
    expect(screen.getByText('标记')).toBeInTheDocument();
  });

  it('access tab shows resource identifier', async () => {
    render(
      <ObjectDetailPanel
        ontologyName="finance"
        objectType={_ot as any}
        onEdit={() => {}}
        onDelete={() => {}}
        onClose={() => {}}
      />,
    );
    await waitFor(() => screen.getByText('访问控制'));
    fireEvent.click(screen.getByText('访问控制'));
    expect(screen.getByText(/OBJECT_TYPE/)).toBeInTheDocument();
  });
});
