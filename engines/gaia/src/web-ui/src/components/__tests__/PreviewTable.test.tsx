import { describe, it, expect } from 'vitest';
import { render, screen } from '@testing-library/react';
import { PreviewTable } from '../PreviewTable';
import type { ColumnInfo } from '../../types';

const cols: ColumnInfo[] = [
  { name: 'id', data_type: 'integer', nullable: false, is_primary_key: true, comment: '' },
  {
    name: 'userName',
    data_type: 'varchar(64)',
    nullable: true,
    is_primary_key: false,
    comment: '用户名（保留原始大小写）',
  },
  { name: 'data', data_type: 'jsonb', nullable: true, is_primary_key: false, comment: '' },
];

// Trino 小写化所有列标识符，返回的 row dict key 全是小写（modelId → modelid）。
// PreviewTable 表头用 col.name（原始大小写，来自 Gravitino REST），取值用
// col.name.toLowerCase() 对齐 Trino 数据。这里 rows 的 key 用小写模拟真实场景。
const rows: Record<string, unknown>[] = [
  { id: 1, username: 'Alice', data: { x: 1 } },
  { id: 2, username: null, data: undefined },
];

describe('PreviewTable', () => {
  it('renders column name + type + comment in the header', () => {
    render(<PreviewTable columns={cols} rows={rows} />);
    // 列名保留原始大小写（不转大写）
    expect(screen.getByText('userName')).toBeInTheDocument();
    // 类型作为副标题
    expect(screen.getByText('varchar(64)')).toBeInTheDocument();
    // 注释展示在表头
    expect(screen.getByText('用户名（保留原始大小写）')).toBeInTheDocument();
  });

  it('renders NULL marker for null/undefined values', () => {
    const { container } = render(<PreviewTable columns={cols} rows={rows} />);
    const nullCells = container.querySelectorAll('.preview-null');
    expect(nullCells).toHaveLength(2); // 第二行 userName=null + data=undefined
  });

  it('serializes object values to JSON', () => {
    const { container } = render(<PreviewTable columns={cols} rows={rows} />);
    expect(container.querySelector('tbody')?.textContent).toContain('{"x":1}');
  });

  it('shows row index column', () => {
    const { container } = render(<PreviewTable columns={cols} rows={rows} />);
    const indexCells = container.querySelectorAll('.preview-row-index');
    // 表头 1 个 + 每行 1 个 = 3
    expect(indexCells).toHaveLength(3);
  });

  it('shows "无数据" when columns empty', () => {
    render(<PreviewTable columns={[]} rows={[]} />);
    expect(screen.getByText('无数据')).toBeInTheDocument();
  });

  it('shows error message', () => {
    render(<PreviewTable columns={cols} rows={[]} error="该表含有暂不支持的列类型" />);
    expect(screen.getByText(/该表含有暂不支持的列类型/)).toBeInTheDocument();
  });

  it('shows loading state', () => {
    render(<PreviewTable columns={cols} rows={[]} loading />);
    expect(screen.getByText('加载数据中…')).toBeInTheDocument();
  });

  it('respects maxRows and shows overflow footer', () => {
    const manyRows = Array.from({ length: 150 }, (_, i) => ({ id: i, username: `u${i}`, data: null }));
    render(<PreviewTable columns={cols} rows={manyRows} maxRows={100} />);
    expect(screen.getByText(/最多显示前 100 行/)).toBeInTheDocument();
    const bodyRows = document.querySelectorAll('tbody tr');
    expect(bodyRows).toHaveLength(100);
  });

  it('shows total count footer when rows are fewer than maxRows', () => {
    // 不足 100 行：显示"共 N 行"，不显示"最多显示前"
    const fewRows = Array.from({ length: 42 }, (_, i) => ({ id: i, username: `u${i}`, data: null }));
    render(<PreviewTable columns={cols} rows={fewRows} maxRows={100} />);
    expect(screen.getByText(/共 42 行/)).toBeInTheDocument();
    expect(screen.queryByText(/最多显示前/)).not.toBeInTheDocument();
  });

  it('shows "may have more" footer when rows exactly equal maxRows', () => {
    // 正好 100 行：无法区分表正好 100 条还是被截断，按"最多显示前 N 行"提示
    const exactRows = Array.from({ length: 100 }, (_, i) => ({ id: i, username: `u${i}`, data: null }));
    render(<PreviewTable columns={cols} rows={exactRows} maxRows={100} />);
    expect(screen.getByText(/最多显示前 100 行/)).toBeInTheDocument();
  });

  it('shows "该表无数据" footer and empty body when rows is empty', () => {
    // 空表：表头照常渲染（让用户看到列结构），表体一行跨列提示，footer 显示"该表无数据"
    const { container } = render(<PreviewTable columns={cols} rows={[]} maxRows={100} />);
    // 表头还在
    expect(screen.getByText('userName')).toBeInTheDocument();
    // 表体跨列空状态
    expect(container.querySelector('.preview-empty')).toBeInTheDocument();
    expect(screen.getByText('暂无数据')).toBeInTheDocument();
    // footer
    expect(screen.getByText('该表无数据')).toBeInTheDocument();
  });
  it('matches mixed-case column names against lowercased row keys (Trino normalization)', () => {
    // Trino 返回的 dict key 是小写 modelid/specialfeatures；列信息（Gravitino REST）
    // 是原始大小写 modelId/SpecialFeatures。表头显示原始大小写，取值用小写 key。
    const mixedCols: ColumnInfo[] = [
      { name: 'modelId', data_type: 'string', nullable: false, is_primary_key: true, comment: '' },
      { name: 'SpecialFeatures', data_type: 'string', nullable: true, is_primary_key: false, comment: '' },
      { name: 'downloads', data_type: 'long', nullable: true, is_primary_key: false, comment: '' },
    ];
    const mixedRows: Record<string, unknown>[] = [
      { modelid: 'deepseek-ai/DeepSeek-R1', specialfeatures: '[]', downloads: 9368615 },
      { modelid: 'gpt-4', specialfeatures: null, downloads: 0 },
    ];
    const { container } = render(<PreviewTable columns={mixedCols} rows={mixedRows} />);

    // 表头保留原始大小写
    expect(screen.getByText('modelId')).toBeInTheDocument();
    expect(screen.getByText('SpecialFeatures')).toBeInTheDocument();

    // 数据正确匹配到（不是 NULL）
    const nullCells = container.querySelectorAll('.preview-null');
    expect(nullCells).toHaveLength(1); // 仅第二行 SpecialFeatures=null

    // 第一行数据正确显示
    const body = container.querySelector('tbody');
    expect(body?.textContent).toContain('deepseek-ai/DeepSeek-R1');
    expect(body?.textContent).toContain('9368615');
  });
});
