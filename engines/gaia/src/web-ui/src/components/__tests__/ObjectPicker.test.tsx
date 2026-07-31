import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import { ObjectPicker } from '../ObjectPicker';
import { searchObjects, getObjectType } from '../../api/client';

// ── Mocks ──

const { searchResults, otMeta } = vi.hoisted(() => ({
  searchResults: [] as Record<string, unknown>[],
  otMeta: {
    primary_key: 'userId',
    title_property: 'userName',
    properties: [],
  },
}));

vi.mock('../../api/client', () => ({
  searchObjects: vi.fn(async () => searchResults),
  getObjectType: vi.fn(async () => otMeta),
}));

const onChange = vi.fn();

function renderPicker(overrides: Partial<React.ComponentProps<typeof ObjectPicker>> = {}) {
  return render(
    <ObjectPicker
      ontology="Marketing"
      objectType="SalesConsultant"
      value=""
      onChange={onChange}
      {...overrides}
    />,
  );
}

describe('ObjectPicker — server-side search', () => {
  beforeEach(() => {
    searchResults.length = 0;
    vi.clearAllMocks();
  });

  it('loads object type metadata (pk/title) on mount', async () => {
    renderPicker();
    await waitFor(() => {
      expect(getObjectType).toHaveBeenCalledWith('Marketing', 'SalesConsultant');
    });
  });

  it('renders a "click to search" placeholder before focus', () => {
    renderPicker();
    const input = screen.getByRole('combobox', { name: /选择 SalesConsultant 对象/ });
    expect(input).toHaveAttribute('placeholder', '点击搜索对象');
  });

  it('forwards caller-provided searchProperties to the search query', async () => {
    // Render + wait for metadata, then trigger search via the combobox's
    // open (onOpenChange). We assert the searchObjects call shape rather
    // than DOM focus timing (React Aria focus is flaky in jsdom).
    renderPicker({ searchProperties: ['userId', 'userName', 'phone'] });
    await waitFor(() => expect(getObjectType).toHaveBeenCalled());
    // The picker wires searchProperties into searchObjects calls; trigger
    // by opening the combobox popover (React Aria menuTrigger=focus).
    const input = screen.getByRole('combobox', { name: /选择 SalesConsultant 对象/ });
    input.focus();
    await waitFor(() => {
      expect(searchObjects).toHaveBeenCalledWith(
        'Marketing',
        'SalesConsultant',
        '',
        expect.arrayContaining(['phone']),
        expect.any(Object),
      );
    });
  });

  it('falls back to [pk, title] when no searchProperties given', async () => {
    renderPicker();
    await waitFor(() => expect(getObjectType).toHaveBeenCalled());
    const input = screen.getByRole('combobox', { name: /选择 SalesConsultant 对象/ });
    input.focus();
    await waitFor(() => {
      expect(searchObjects).toHaveBeenCalledWith(
        'Marketing',
        'SalesConsultant',
        '',
        expect.arrayContaining(['userId', 'userName']),
        expect.objectContaining({ limit: 20 }),
      );
    });
  });
});
