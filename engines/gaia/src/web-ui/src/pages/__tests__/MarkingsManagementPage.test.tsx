import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import { MarkingsManagementPage } from '../MarkingsManagementPage';
import * as permApi from '../../api/permission';
import { ApiError } from '../../api/client';

vi.mock('../../api/client', () => ({
  ApiError: class ApiError extends Error {
    status: number;
    constructor(message: string, status: number) {
      super(message);
      this.status = status;
    }
  },
}));

const _cats = [
  { id: 'c1', name: '密级', description: '', is_system: false, created_at: '', updated_at: '' },
  { id: 'c2', name: '部门', description: '', is_system: true, created_at: '', updated_at: '' },
];
const _marks = [
  { id: 'm1', category_id: 'c1', name: 'PUBLIC', display_name: '公开', description: '', is_system: false, source_organization_id: null, created_at: '', updated_at: '' },
  { id: 'm2', category_id: 'c1', name: 'CONFIDENTIAL', display_name: '机密', description: '受限', is_system: false, source_organization_id: null, created_at: '', updated_at: '' },
];

describe('MarkingsManagementPage', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    vi.spyOn(permApi, 'listMarkingCategories').mockResolvedValue(_cats);
    vi.spyOn(permApi, 'listMarkings').mockResolvedValue(_marks);
  });

  it('lists categories and markings on load', async () => {
    render(<MarkingsManagementPage />);
    await waitFor(() => {
      expect(screen.getByText('密级')).toBeInTheDocument();
      expect(screen.getByText('部门')).toBeInTheDocument();
    });
    // All markings visible by default (no category selected).
    expect(screen.getByText('PUBLIC')).toBeInTheDocument();
    expect(screen.getByText('CONFIDENTIAL')).toBeInTheDocument();
  });

  it('filters markings by selected category', async () => {
    render(<MarkingsManagementPage />);
    await waitFor(() => screen.getByText('密级'));

    fireEvent.click(screen.getByText('密级'));
    // Both markings belong to c1, still visible.
    expect(screen.getByText('PUBLIC')).toBeInTheDocument();
    expect(screen.getByText('CONFIDENTIAL')).toBeInTheDocument();
  });

  it('shows create-category form on "+ 新建分类" click', async () => {
    render(<MarkingsManagementPage />);
    await waitFor(() => screen.getByText('密级'));

    fireEvent.click(screen.getByText('+ 新建分类'));
    expect(screen.getByPlaceholderText('分类名（如 密级）')).toBeInTheDocument();
  });

  it('creates a category and reloads', async () => {
    const createSpy = vi.spyOn(permApi, 'createMarkingCategory').mockResolvedValue(_cats[0]);
    render(<MarkingsManagementPage />);
    await waitFor(() => screen.getByText('密级'));

    fireEvent.click(screen.getByText('+ 新建分类'));
    fireEvent.change(screen.getByPlaceholderText('分类名（如 密级）'), { target: { value: '区域' } });
    fireEvent.click(screen.getByText('创建'));

    await waitFor(() => {
      expect(createSpy).toHaveBeenCalledWith('区域', '');
    });
  });

  it('shows create-marking form only when a category is selected', async () => {
    render(<MarkingsManagementPage />);
    await waitFor(() => screen.getByText('密级'));

    // No category selected → no "+ 新建标记" button.
    expect(screen.queryByText('+ 新建标记')).not.toBeInTheDocument();

    fireEvent.click(screen.getByText('密级'));
    expect(screen.getByText('+ 新建标记')).toBeInTheDocument();
  });

  it('creates a marking under the selected category', async () => {
    const createMarkSpy = vi.spyOn(permApi, 'createMarking').mockResolvedValue(_marks[0]);
    render(<MarkingsManagementPage />);
    await waitFor(() => screen.getByText('密级'));

    fireEvent.click(screen.getByText('密级'));
    fireEvent.click(screen.getByText('+ 新建标记'));
    fireEvent.change(screen.getByPlaceholderText('标记名（如 CONFIDENTIAL）'), { target: { value: 'SECRET' } });
    fireEvent.click(screen.getByText('创建'));

    await waitFor(() => {
      expect(createMarkSpy).toHaveBeenCalledWith(
        expect.objectContaining({ category_id: 'c1', name: 'SECRET' }),
      );
    });
  });

  it('shows grant form on "授予 Group" click', async () => {
    render(<MarkingsManagementPage />);
    await waitFor(() => screen.getByText('CONFIDENTIAL'));

    fireEvent.click(screen.getAllByText('授予 Group')[0]);
    expect(screen.getByPlaceholderText('group-uuid')).toBeInTheDocument();
  });

  it('grants a marking to a group', async () => {
    const grantSpy = vi.spyOn(permApi, 'grantMarking').mockResolvedValue({ status: 'granted' });
    render(<MarkingsManagementPage />);
    await waitFor(() => screen.getByText('CONFIDENTIAL'));

    // Click the first "授予 Group" button.
    fireEvent.click(screen.getAllByText('授予 Group')[0]);
    fireEvent.change(screen.getByPlaceholderText('group-uuid'), { target: { value: 'grp-123' } });
    fireEvent.click(screen.getByText('授予'));

    await waitFor(() => {
      expect(grantSpy).toHaveBeenCalledWith(expect.any(String), 'grp-123');
      expect(screen.getByText('授予成功')).toBeInTheDocument();
    });
  });

  it('shows error on create failure', async () => {
    vi.spyOn(permApi, 'createMarkingCategory').mockRejectedValue(
      new ApiError('conflict', 409),
    );
    render(<MarkingsManagementPage />);
    await waitFor(() => screen.getByText('密级'));

    fireEvent.click(screen.getByText('+ 新建分类'));
    fireEvent.change(screen.getByPlaceholderText('分类名（如 密级）'), { target: { value: 'dup' } });
    fireEvent.click(screen.getByText('创建'));

    await waitFor(() => {
      expect(screen.getByText(/已存在|冲突|conflict/)).toBeInTheDocument();
    });
  });
});
