import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { CreateObjectWizard } from '../CreateObjectWizard';
import type { ScaffoldResult, ScaffoldErrorFrame } from '../../api/ai';

// ── Mocks ──

// scaffoldObjectType is an async generator; tests control its yield
// sequence. Shared state must be hoisted so the vi.mock factory (which is
// itself hoisted above imports) can reference it.
const { scaffoldYields, scaffoldCalls } = vi.hoisted(() => ({
  scaffoldYields: [] as (ScaffoldResult | ScaffoldErrorFrame)[],
  scaffoldCalls: { current: 0 },
}));

vi.mock('../../api/ai', () => ({
  scaffoldObjectType: vi.fn(async function* () {
    scaffoldCalls.current++;
    for (const frame of scaffoldYields) {
      yield frame;
    }
  }),
}));

vi.mock('../../api/client', () => ({
  listDatasets: vi.fn(async () => [
    {
      id: 'd1',
      api_name: 'customer',
      display_name: '客户表',
      storage_location: '',
      partition_config: null,
      source_dataset_api_name: null,
      data_source_api_name: null,
      kind: 'MANAGED',
      is_view: false,
      row_count_estimate: 100,
      created_at: '',
      updated_at: '',
    },
  ]),
  getDatasetSchema: vi.fn(async () => ({
    columns: [
      { name: 'customer_id', type: 'bigint', nullable: false },
      { name: 'name', type: 'varchar', nullable: false },
      { name: 'email', type: 'varchar', nullable: true },
    ],
  })),
}));

import { listDatasets } from '../../api/client';

const onComplete = vi.fn();
const onCancel = vi.fn();

function renderWizard() {
  return render(<CreateObjectWizard onComplete={onComplete} onCancel={onCancel} />);
}

describe('CreateObjectWizard — BuildWith scaffold', () => {
  beforeEach(() => {
    scaffoldYields.length = 0;
    scaffoldCalls.current = 0;
    onComplete.mockClear();
    onCancel.mockClear();
  });

  it('listDatasets mock returns the customer dataset (sanity check)', async () => {
    const ds = await listDatasets();
    expect(ds).toHaveLength(1);
    expect(ds[0].api_name).toBe('customer');
    expect(ds[0].kind).toBe('MANAGED');
  });

  it('renders 3 steps (datasource → properties → review)', async () => {
    renderWizard();
    // Storage type buttons are static (don't depend on dataset loading).
    expect(screen.getByText('托管对象 MANAGED')).toBeInTheDocument();
    // Step titles appear in the sidebar; “选择数据集” appears both as the
    // sidebar item and the step heading.
    const items = await screen.findAllByText('选择数据集');
    expect(items.length).toBeGreaterThanOrEqual(1);
    expect(screen.getByText('配置属性')).toBeInTheDocument();
    expect(screen.getByText('审核并创建')).toBeInTheDocument();
    // Removed steps are gone
    expect(screen.queryByText('设置关系')).not.toBeInTheDocument();
    expect(screen.queryByText('配置操作')).not.toBeInTheDocument();
  });

  it('scaffolds the object structure after a dataset is selected, filling metadata + properties + keys', async () => {
    // Final complete frame from /ai/scaffold (already sanitized backend-side).
    scaffoldYields.push({
      display_name: '客户',
      api_name: 'Customer',
      description: '客户信息',
      primary_key_column: 'customer_id',
      title_column: 'name',
      properties: [
        {
          source_column: 'customer_id',
          display_name: '客户ID',
          description: '',
          searchable: false,
          is_primary_key: true,
          is_title_property: false,
        },
        {
          source_column: 'name',
          display_name: '姓名',
          description: '',
          searchable: true,
          is_primary_key: false,
          is_title_property: true,
        },
        {
          source_column: 'email',
          display_name: '邮箱',
          description: '',
          searchable: true,
          is_primary_key: false,
          is_title_property: false,
        },
      ],
    });

    renderWizard();

    // Verify the dataset catalog mock loaded.
    await waitFor(() => {
      expect(listDatasets).toHaveBeenCalled();
    });

    // Step 0: select the dataset.
    const datasetItem = await screen.findByTestId('dataset-option-customer');
    fireEvent.click(datasetItem);
    // Advance to Step 1 to see the scaffolded structure.
    fireEvent.click(screen.getByText('Next →'));

    // Wait for scaffold to run + Step 1 to show the derived display name.
    await waitFor(() => {
      expect(screen.getByDisplayValue('客户')).toBeInTheDocument();
    });

    // api_name filled.
    expect(screen.getByDisplayValue('Customer')).toBeInTheDocument();

    // Properties table shows the derived display names.
    await waitFor(() => {
      expect(screen.getByText('3 defined')).toBeInTheDocument();
    });
    expect(screen.getAllByDisplayValue('客户ID').length).toBeGreaterThan(0);
    expect(screen.getAllByDisplayValue('姓名').length).toBeGreaterThan(0);
    expect(screen.getAllByDisplayValue('邮箱').length).toBeGreaterThan(0);

    expect(scaffoldCalls.current).toBe(1);
  });

  it('falls back to a deterministic skeleton when scaffold yields an error frame', async () => {
    scaffoldYields.push({ error: 'model unavailable' });

    renderWizard();
    const datasetItem = await screen.findByTestId('dataset-option-customer');
    fireEvent.click(datasetItem);
    // Advance to Step 1 to see the scaffolded structure.
    fireEvent.click(screen.getByText('Next →'));

    // Fallback skeleton uses column names as display names (not Chinese).
    await waitFor(() => {
      expect(screen.getByDisplayValue('customer_id')).toBeInTheDocument();
    });
    expect(screen.getByDisplayValue('name')).toBeInTheDocument();
    expect(screen.getByDisplayValue('email')).toBeInTheDocument();

    // Fallback banner shown.
    expect(screen.getByText(/AI 推导失败，已生成基础结构/)).toBeInTheDocument();
  });

  it('progressively fills properties as partial frames stream in', async () => {
    // First partial: metadata + one property, no keys yet.
    scaffoldYields.push({
      display_name: '客户',
      api_name: 'Customer',
      description: '客户信息',
      properties: [
        {
          source_column: 'customer_id',
          display_name: '客户ID',
          description: '',
          searchable: false,
          is_primary_key: false,
          is_title_property: false,
        },
      ],
    });
    // Final frame: complete with keys.
    scaffoldYields.push({
      display_name: '客户',
      api_name: 'Customer',
      description: '客户信息',
      primary_key_column: 'customer_id',
      title_column: 'name',
      properties: [
        {
          source_column: 'customer_id',
          display_name: '客户ID',
          description: '',
          searchable: false,
          is_primary_key: true,
          is_title_property: false,
        },
        {
          source_column: 'name',
          display_name: '姓名',
          description: '',
          searchable: true,
          is_primary_key: false,
          is_title_property: true,
        },
        {
          source_column: 'email',
          display_name: '邮箱',
          description: '',
          searchable: true,
          is_primary_key: false,
          is_title_property: false,
        },
      ],
    });

    renderWizard();
    const datasetItem = await screen.findByTestId('dataset-option-customer');
    fireEvent.click(datasetItem);
    // Advance to Step 1 to see the scaffolded structure.
    fireEvent.click(screen.getByText('Next →'));

    // First property appears early.
    await waitFor(() => {
      expect(screen.getAllByDisplayValue('客户ID').length).toBeGreaterThan(0);
    });
    // After the final frame, all three properties present.
    await waitFor(() => {
      expect(screen.getAllByDisplayValue('邮箱').length).toBeGreaterThan(0);
    });
  });
});

describe('CreateObjectWizard — edit mode', () => {
  beforeEach(() => {
    scaffoldYields.length = 0;
    scaffoldCalls.current = 0;
    onComplete.mockClear();
    onCancel.mockClear();
  });

  it('shows metadata on Step 0 (基础信息与数据集), not on the properties step', () => {
    render(
      <CreateObjectWizard
        editing
        initialData={{
          api_name: 'VehicleModel',
          display_name: '车型版本',
          description: '车型版本主数据。',
          storage_type: 'MANAGED',
          dataset_api_name: 'vehicle_model',
          properties: [
            { display_name: 'ID', description: '', data_type: 'STRING', is_primary_key: true, is_title_property: false, searchable: false, nullable: false },
          ],
        }}
        onComplete={onComplete}
        onCancel={onCancel}
      />,
    );

    // Step 0 is the overview step in edit mode (sidebar + heading).
    expect(screen.getAllByText('基础信息与数据集').length).toBeGreaterThanOrEqual(1);
    // Metadata fields render on Step 0.
    expect(screen.getByDisplayValue('车型版本')).toBeInTheDocument();
    expect(screen.getByDisplayValue('VehicleModel')).toBeInTheDocument();
    expect(screen.getByDisplayValue('车型版本主数据。')).toBeInTheDocument();
    // storage_type locked indicator.
    expect(screen.getByText(/创建后不可更改/)).toBeInTheDocument();
  });

  it('locks api_name read-only in edit mode', () => {
    render(
      <CreateObjectWizard
        editing
        initialData={{
          api_name: 'VehicleModel',
          display_name: '车型版本',
          storage_type: 'MANAGED',
          dataset_api_name: 'vehicle_model',
          properties: [
            { display_name: 'ID', description: '', data_type: 'STRING', is_primary_key: true, is_title_property: false, searchable: false, nullable: false },
          ],
        }}
        onComplete={onComplete}
        onCancel={onCancel}
      />,
    );

    const apiInput = screen.getByDisplayValue('VehicleModel');
    expect(apiInput).toBeDisabled();
    // The AI suggest button is absent in edit mode.
    expect(screen.queryByText('✨ AI')).not.toBeInTheDocument();
  });

  it('does not show the storage_type segmented control in edit mode', () => {
    render(
      <CreateObjectWizard
        editing
        initialData={{
          api_name: 'VehicleModel',
          display_name: '车型版本',
          storage_type: 'MANAGED',
          dataset_api_name: 'vehicle_model',
          properties: [
            { display_name: 'ID', description: '', data_type: 'STRING', is_primary_key: true, is_title_property: false, searchable: false, nullable: false },
          ],
        }}
        onComplete={onComplete}
        onCancel={onCancel}
      />,
    );
    // The create-mode segmented buttons are gone.
    expect(screen.queryByText('托管对象 MANAGED')).not.toBeInTheDocument();
    expect(screen.queryByText('虚拟对象 VIRTUAL')).not.toBeInTheDocument();
  });

  it('blocks Next on the properties step when properties lack source_column (dataset bound)', async () => {
    // Edit mode, dataset bound, but properties have NO source_column.
    // → unmapped → warning shown on the properties step + Next disabled
    // (blocked before reaching review).
    const user = userEvent.setup();
    render(
      <CreateObjectWizard
        editing
        initialData={{
          api_name: 'VehicleModel',
          display_name: '车型版本',
          storage_type: 'MANAGED',
          dataset_api_name: 'vehicle_model',
          properties: [
            // PK + title on separate props so PK/title checks pass.
            { display_name: 'ID', description: '', data_type: 'STRING', is_primary_key: true, is_title_property: false, searchable: false, nullable: false },
            { display_name: 'Name', description: '', data_type: 'STRING', is_primary_key: false, is_title_property: true, searchable: false, nullable: false },
          ],
        }}
        onComplete={onComplete}
        onCancel={onCancel}
      />,
    );

    // Step 0 → Step 1 (properties)
    await user.click(screen.getByText('Next →'));

    // Properties step: warning shown + Next disabled.
    await waitFor(() => {
      expect(screen.getByText(/还有 2 个属性未映射源列/)).toBeInTheDocument();
    });
    const nextBtn = screen.getByText('Next →');
    expect(nextBtn).toBeDisabled();
  });
});
