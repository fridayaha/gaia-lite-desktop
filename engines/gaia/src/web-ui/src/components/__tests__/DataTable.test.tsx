import { describe, it, expect, vi } from 'vitest';
import { render, fireEvent } from '@testing-library/react';
import { DataTable, type DataTableColumn } from '../ui/DataTable';

interface Row {
  id: string;
  name: string;
  age: number;
}

const columns: DataTableColumn[] = [
  { id: 'name', label: '名称' },
  { id: 'age', label: '年龄', cellClassName: 'w-[60px]' },
];

const rows: Row[] = [
  { id: 'a', name: 'Alice', age: 30 },
  { id: 'b', name: 'Bob', age: 25 },
];

function getBodyRows(container: HTMLElement): HTMLElement[] {
  // Query native <tr> inside <tbody> directly — jsdom's implicit ARIA role
  // mapping for <tr> is inconsistent, so role-based queries are unreliable.
  return Array.from(container.querySelectorAll('tbody tr'));
}

describe('DataTable', () => {
  it('renders a native semantic table with headers and cells', () => {
    const { container } = render(
      <DataTable
        aria-label="员工"
        columns={columns}
        rows={rows}
        rowKey={(r) => r.id}
        renderCell={(r, colId) => (colId === 'name' ? r.name : String(r.age))}
      />,
    );
    const table = container.querySelector('table');
    expect(table).not.toBeNull();
    expect(table).toHaveAttribute('aria-label', '员工');
    // Column headers
    const ths = Array.from(container.querySelectorAll('thead th'));
    expect(ths.map((th) => th.textContent)).toEqual(['名称', '年龄']);
    // Body rows + cells
    const bodyRows = getBodyRows(container);
    expect(bodyRows).toHaveLength(2);
    expect(bodyRows[0].querySelectorAll('td')[0]).toHaveTextContent('Alice');
    expect(bodyRows[0].querySelectorAll('td')[1]).toHaveTextContent('30');
  });

  it('falls back to row index as key when rowKey is omitted', () => {
    const { container } = render(
      <DataTable
        columns={columns}
        rows={rows}
        renderCell={(r, colId) => (colId === 'name' ? r.name : String(r.age))}
      />,
    );
    // No crash, both rows render
    expect(getBodyRows(container)).toHaveLength(2);
  });

  it('renders an empty tbody when rows is empty (header still shown)', () => {
    const { container } = render(
      <DataTable columns={columns} rows={[]} renderCell={() => null} />,
    );
    expect(container.querySelectorAll('thead th')).toHaveLength(2);
    expect(getBodyRows(container)).toHaveLength(0);
  });

  it('triggers onRowAction on row click', () => {
    const onRowAction = vi.fn();
    const { container } = render(
      <DataTable
        columns={columns}
        rows={rows}
        rowKey={(r) => r.id}
        onRowAction={onRowAction}
        renderCell={(r, colId) => (colId === 'name' ? r.name : String(r.age))}
      />,
    );
    const bodyRows = getBodyRows(container);
    // Interactive rows are keyboard-focusable buttons
    expect(bodyRows[0]).toHaveAttribute('role', 'button');
    expect(bodyRows[0]).toHaveAttribute('tabindex', '0');
    fireEvent.click(bodyRows[0]);
    expect(onRowAction).toHaveBeenCalledWith('a');
  });

  it('triggers onRowAction on Enter / Space (keyboard activation)', () => {
    const onRowAction = vi.fn();
    const { container } = render(
      <DataTable
        columns={columns}
        rows={rows}
        rowKey={(r) => r.id}
        onRowAction={onRowAction}
        renderCell={(r, colId) => (colId === 'name' ? r.name : String(r.age))}
      />,
    );
    const bodyRows = getBodyRows(container);
    fireEvent.keyDown(bodyRows[1], { key: 'Enter' });
    expect(onRowAction).toHaveBeenCalledWith('b');
    fireEvent.keyDown(bodyRows[1], { key: ' ' });
    expect(onRowAction).toHaveBeenCalledTimes(2);
    // Other keys do nothing
    fireEvent.keyDown(bodyRows[1], { key: 'ArrowDown' });
    expect(onRowAction).toHaveBeenCalledTimes(2);
  });

  it('does not attach interactive semantics when onRowAction is absent', () => {
    const { container } = render(
      <DataTable
        columns={columns}
        rows={rows}
        rowKey={(r) => r.id}
        renderCell={(r, colId) => (colId === 'name' ? r.name : String(r.age))}
      />,
    );
    const bodyRows = getBodyRows(container);
    expect(bodyRows[0]).not.toHaveAttribute('role');
    expect(bodyRows[0]).not.toHaveAttribute('tabindex');
  });

  it('applies rowClassName and column cellClassName', () => {
    const { container } = render(
      <DataTable
        columns={columns}
        rows={rows}
        rowKey={(r) => r.id}
        rowClassName={(r) => (r.age > 28 ? 'mature' : undefined)}
        renderCell={(r, colId) => (colId === 'name' ? r.name : String(r.age))}
      />,
    );
    const trs = getBodyRows(container);
    expect(trs[0]).toHaveClass('mature');
    expect(trs[1]).not.toHaveClass('mature');
    // column cellClassName applied to header
    const ths = container.querySelectorAll('thead th');
    expect(ths[1]).toHaveClass('w-[60px]');
  });
});
