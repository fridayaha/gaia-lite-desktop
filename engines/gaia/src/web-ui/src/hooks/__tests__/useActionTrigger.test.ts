/**
 * useActionTrigger hook 测试（分析→行动闭环）。
 */
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { renderHook, act, waitFor } from '@testing-library/react';
import { useActionTrigger } from '../useActionTrigger';
import type { ActionTypeRecord, ObjectTypeSummary } from '../../types';

vi.mock('../../api/client', () => ({
  listActionTypes: vi.fn(),
}));

import { listActionTypes } from '../../api/client';
const mockList = listActionTypes as ReturnType<typeof vi.fn>;

const ot: ObjectTypeSummary = {
  id: 'ot-1', ontology_id: 'ont-1', api_name: 'Supplier', display_name: 'Supplier',
  description: '', storage_type: 'MANAGED', visibility: 'PUBLIC', status: 'ACTIVE',
  properties_count: 1, links_count: 1, actions_count: 2, created_at: '', updated_at: '',
} as unknown as ObjectTypeSummary;

function makeAction(id: string, otId: string, status = 'ACTIVE'): ActionTypeRecord {
  return {
    id, api_name: id, display_name: id, description: '',
    affected_object_type_id: otId, parameters: {}, rules: {}, submission_criteria: {},
    status, ontology_id: 'ont-1', created_at: '', updated_at: '',
  } as unknown as ActionTypeRecord;
}

describe('useActionTrigger', () => {
  beforeEach(() => vi.clearAllMocks());

  it('过滤出匹配对象类型的 ACTIVE Action', async () => {
    mockList.mockResolvedValue([
      makeAction('mark_risk', 'ot-1'),
      makeAction('other', 'ot-2'),
      makeAction('disabled', 'ot-1', 'INACTIVE'),
    ]);
    const { result } = renderHook(() => useActionTrigger('ONT', ot));
    await waitFor(() => expect(result.current.applicableActions).toHaveLength(1));
    expect(result.current.applicableActions[0].api_name).toBe('mark_risk');
  });

  it('VIRTUAL 目标类型不显示操作', async () => {
    mockList.mockResolvedValue([makeAction('mark_risk', 'ot-1')]);
    const virtualOt = { ...ot, storage_type: 'VIRTUAL' as const };
    const { result } = renderHook(() => useActionTrigger('ONT', virtualOt));
    await waitFor(() => expect(result.current.loading).toBe(false));
    expect(result.current.applicableActions).toHaveLength(0);
  });

  it('trigger 设置 execAction，close 清除', async () => {
    mockList.mockResolvedValue([makeAction('mark_risk', 'ot-1')]);
    const { result } = renderHook(() => useActionTrigger('ONT', ot));
    await waitFor(() => expect(result.current.applicableActions).toHaveLength(1));
    act(() => result.current.trigger(result.current.applicableActions[0]));
    expect(result.current.execAction).not.toBeNull();
    act(() => result.current.close());
    expect(result.current.execAction).toBeNull();
  });

  it('selectedOt 为 null 时操作列表为空', () => {
    mockList.mockResolvedValue([]);
    const { result } = renderHook(() => useActionTrigger('ONT', null));
    expect(result.current.applicableActions).toHaveLength(0);
  });
});
