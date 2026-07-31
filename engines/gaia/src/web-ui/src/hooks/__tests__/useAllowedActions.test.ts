import { describe, it, expect, vi, beforeEach } from 'vitest';
import { renderHook, act } from '@testing-library/react';
import { useAllowedActions } from '../useAllowedActions';
import * as permApi from '../../api/permission';

vi.useFakeTimers();

describe('useAllowedActions', () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it('fetches decisions for the given resource ids', async () => {
    const spy = vi
      .spyOn(permApi, 'getAllowedActions')
      .mockResolvedValue({
        'ds-1': { allowedActions: ['datasource:view'], disabledReasons: { 'datasource:delete': '无权限' } },
      });

    const { result } = renderHook(() => useAllowedActions('DATASOURCE', ['ds-1']));

    // Initially loading.
    expect(result.current.loading).toBe(true);

    // Flush the promise.
    await act(async () => {
      await vi.runAllTimersAsync();
    });

    expect(spy).toHaveBeenCalledWith('DATASOURCE', ['ds-1']);
    expect(result.current.loading).toBe(false);
    expect(result.current.isAllowed('ds-1', 'datasource:view')).toBe(true);
    expect(result.current.isAllowed('ds-1', 'datasource:delete')).toBe(false);
    expect(result.current.disabledReason('ds-1', 'datasource:delete')).toBe('无权限');
  });

  it('returns false for unknown resource ids', async () => {
    vi.spyOn(permApi, 'getAllowedActions').mockResolvedValue({});
    const { result } = renderHook(() => useAllowedActions('DATASOURCE', ['ds-1']));

    await act(async () => {
      await vi.runAllTimersAsync();
    });

    expect(result.current.isAllowed('unknown', 'datasource:view')).toBe(false);
    expect(result.current.disabledReason('unknown', 'datasource:view')).toBe('');
  });

  it('skips fetch when resource ids is empty', () => {
    const spy = vi.spyOn(permApi, 'getAllowedActions');
    const { result } = renderHook(() => useAllowedActions('DATASOURCE', []));

    expect(spy).not.toHaveBeenCalled();
    expect(result.current.loading).toBe(false);
    expect(result.current.decisions).toEqual({});
  });

  it('handles fetch errors', async () => {
    vi.spyOn(permApi, 'getAllowedActions').mockRejectedValue(new Error('network'));
    const { result } = renderHook(() => useAllowedActions('DATASOURCE', ['ds-1']));

    await act(async () => {
      await vi.runAllTimersAsync();
    });

    expect(result.current.loading).toBe(false);
    expect(result.current.error).toBeTruthy();
  });

  it('does not refetch when ids are reordered', async () => {
    const spy = vi.spyOn(permApi, 'getAllowedActions').mockResolvedValue({});
    const { rerender } = renderHook(({ ids }) => useAllowedActions('DATASOURCE', ids), {
      initialProps: { ids: ['ds-1', 'ds-2'] },
    });

    await act(async () => {
      await vi.runAllTimersAsync();
    });
    expect(spy).toHaveBeenCalledTimes(1);

    // Reorder — same set, should NOT refetch.
    rerender({ ids: ['ds-2', 'ds-1'] });
    expect(spy).toHaveBeenCalledTimes(1);
  });
});
