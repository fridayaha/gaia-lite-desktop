import { describe, it, expect, vi } from 'vitest';
import { render, screen, fireEvent } from '@testing-library/react';
import { ExplorerView, type SchemaNode } from '../ExplorerView';
import type { ColumnInfo, TableInfo } from '../../types';

const columns: ColumnInfo[] = [
  { name: 'id', data_type: 'integer', nullable: false, is_primary_key: true, comment: '' },
  { name: 'name', data_type: 'text', nullable: true, is_primary_key: false, comment: '名称' },
];

const tableInfo: TableInfo = {
  name: 'users',
  schema: 'public',
  row_count_estimate: 42,
  columns,
  comment: '用户表',
};

const schemas: SchemaNode[] = [{ schema_name: 'public', tables: [tableInfo] }];

const sampleData = {
  rows: [{ id: 1, name: 'Alice' }],
};

const noopCreateSync = vi.fn();

describe('ExplorerView detail tabs', () => {
  it('defaults to 列信息 tab showing ColumnList', () => {
    render(
      <ExplorerView
        schemas={schemas}
        columnMap={{ users: tableInfo }}
        sampleData={sampleData}
        onCreateSync={noopCreateSync}
      />,
    );
    // 点击左侧表名激活详情区
    fireEvent.click(screen.getByText('users'));
    // 列信息 tab 默认激活，应能看到列名
    expect(screen.getByText('id')).toBeInTheDocument();
    expect(screen.getByText('name')).toBeInTheDocument();
    // 数据预览 tab 存在但未激活
    expect(screen.getByRole('tab', { name: /数据预览/ })).toHaveAttribute(
      'aria-selected',
      'false',
    );
  });

  it('switches to 数据预览 tab and shows PreviewTable', () => {
    render(
      <ExplorerView
        schemas={schemas}
        columnMap={{ users: tableInfo }}
        sampleData={sampleData}
        onCreateSync={noopCreateSync}
      />,
    );
    fireEvent.click(screen.getByText('users'));
    // 切到数据预览 tab
    fireEvent.click(screen.getByRole('tab', { name: /数据预览/ }));
    // 预览表出现行数据（列信息 tab 不含行数据）
    expect(screen.getByText('Alice')).toBeInTheDocument();
    // tab 状态正确
    expect(screen.getByRole('tab', { name: /数据预览/ })).toHaveAttribute(
      'aria-selected',
      'true',
    );
  });

  it('switching tables resets to 列信息 tab', () => {
    const table2: TableInfo = {
      name: 'orders',
      schema: 'public',
      row_count_estimate: 0,
      columns: [{ name: 'order_id', data_type: 'bigint', nullable: false, is_primary_key: true, comment: '' }],
      comment: '',
    };
    const schemas2: SchemaNode[] = [
      { schema_name: 'public', tables: [tableInfo, table2] },
    ];
    render(
      <ExplorerView
        schemas={schemas2}
        columnMap={{ users: tableInfo, orders: table2 }}
        sampleData={sampleData}
        onTableClick={vi.fn()}
        onCreateSync={noopCreateSync}
      />,
    );
    // 激活 users，切到数据预览
    fireEvent.click(screen.getByText('users'));
    fireEvent.click(screen.getByRole('tab', { name: /数据预览/ }));
    expect(screen.getByRole('tab', { name: /数据预览/ })).toHaveAttribute(
      'aria-selected',
      'true',
    );
    // 切到 orders
    fireEvent.click(screen.getByText('orders'));
    // tab 应回到列信息
    expect(screen.getByRole('tab', { name: /数据预览/ })).toHaveAttribute(
      'aria-selected',
      'false',
    );
    expect(screen.getByRole('tab', { name: /列信息/ })).toHaveAttribute(
      'aria-selected',
      'true',
    );
  });
});
