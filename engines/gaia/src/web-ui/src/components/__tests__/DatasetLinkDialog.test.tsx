import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { DatasetLinkDialog } from '../DatasetLinkDialog';
import type { ObjectType, DatasetGovernance } from '../../types';

// ── Mocks ──

const { schemaColumns, aiSuggestions } = vi.hoisted(() => ({
  schemaColumns: [] as { name: string; type: string; nullable: boolean }[],
  aiSuggestions: [] as {
    property_api_name: string;
    column_name: string;
    confidence: 'high' | 'medium' | 'low';
  }[],
}));

vi.mock('../../api/client', () => ({
  linkDataset: vi.fn(async () => ({})),
  getDatasetSchema: vi.fn(async () => ({ columns: schemaColumns })),
}));

vi.mock('../../api/ai', async () => {
  const actual = await vi.importActual<typeof import('../../api/ai')>('../../api/ai');
  return {
    ...actual,
    suggestColumnMappings: vi.fn(async () => aiSuggestions),
  };
});

vi.mock('../../hooks/useToast', () => ({
  useToast: () => ({ show: vi.fn() }),
}));

import { linkDataset, getDatasetSchema } from '../../api/client';
import { suggestColumnMappings } from '../../api/ai';

// ── Fixtures ──

function makeObjectType(opts: {
  bound?: string;
  properties?: {
    api_name: string;
    display_name: string;
    data_type: any;
    backing_column?: string;
    is_primary_key?: boolean;
  }[];
}): ObjectType {
  const bound = opts.bound ?? '';
  const props = opts.properties ?? [
    { api_name: 'customerId', display_name: '客户ID', data_type: 'LONG', backing_column: 'customer_id', is_primary_key: true },
    { api_name: 'status', display_name: '状态', data_type: 'STRING', backing_column: 'status_cd' },
    { api_name: 'createdAt', display_name: '创建时间', data_type: 'TIMESTAMP' },
  ];
  return {
    id: 'ot1',
    ontology_id: 'o1',
    api_name: 'Order',
    display_name: '订单',
    description: '',
    primary_key: 'customerId',
    title_property: 'customerId',
    storage_type: 'MANAGED',
    visibility: 'NORMAL',
    status: 'ACTIVE',
    backing_dataset_api_name: bound || null,
    capabilities: { graph_indexing_enabled: false, geotime_indexing_enabled: false },
    properties: props.map((p) => ({
      id: 'p_' + p.api_name,
      object_type_id: 'ot1',
      api_name: p.api_name,
      display_name: p.display_name,
      description: '',
      data_type: p.data_type,
      is_primary_key: p.is_primary_key ?? false,
      is_title_property: false,
      nullable: true,
      indexed: false,
      backing_mapping: p.backing_column
        ? {
            dataset_api_name: bound,
            backing_catalog: 'iceberg',
            backing_schema: 'ontology',
            backing_table: bound,
            backing_column: p.backing_column,
          }
        : null,
      status: 'ACTIVE',
      created_at: '',
      updated_at: '',
    })),
    links: [],
    created_at: '',
    updated_at: '',
  };
}

const datasets: DatasetGovernance[] = [
  {
    id: 'd1',
    api_name: 'order_v1',
    display_name: '订单表v1',
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
  {
    id: 'd2',
    api_name: 'order_v2',
    display_name: '订单表v2',
    storage_location: '',
    partition_config: null,
    source_dataset_api_name: null,
    data_source_api_name: null,
    kind: 'MANAGED',
    is_view: false,
    row_count_estimate: 200,
    created_at: '',
    updated_at: '',
  },
];

const onClose = vi.fn();
const onSaved = vi.fn();

function renderDialog(props: { objectType: ObjectType; open?: boolean }) {
  return render(
    <DatasetLinkDialog
      open={props.open ?? true}
      objectType={props.objectType}
      datasets={datasets}
      ontologyName="Manuf"
      onClose={onClose}
      onSaved={onSaved}
    />,
  );
}

/** Open the React Aria Select (by aria-label) and pick the option whose label
 *  matches. React Aria renders a button trigger + a popover with option roles. */
async function pickFromSelect(user: ReturnType<typeof userEvent.setup>, selectLabel: string, optionLabel: string) {
  const trigger = screen.getByRole('button', { name: new RegExp(selectLabel) });
  await user.click(trigger);
  // The option's accessible name is its label (the SelectOption label prop).
  const option = await screen.findByRole('option', { name: new RegExp(optionLabel) });
  await user.click(option);
}

/** Read the currently-displayed value of a React Aria Select trigger. */
function selectTriggerText(label: string): string {
  return screen.getByRole('button', { name: new RegExp(label) }).textContent ?? '';
}

describe('DatasetLinkDialog', () => {
  beforeEach(() => {
    schemaColumns.length = 0;
    aiSuggestions.length = 0;
    vi.clearAllMocks();
  });

  it('renders the management title when already bound (no switch yet)', () => {
    const ot = makeObjectType({ bound: 'order_v1' });
    renderDialog({ objectType: ot });
    expect(screen.getByText('管理数据集关联')).toBeInTheDocument();
  });

  it('renders the migration title + banner when switching to a different dataset', async () => {
    const user = userEvent.setup();
    const ot = makeObjectType({ bound: 'order_v1' });
    schemaColumns.push(
      { name: 'customer_id', type: 'bigint', nullable: false },
      { name: 'order_status', type: 'varchar', nullable: true },
    );
    renderDialog({ objectType: ot });

    await pickFromSelect(user, '选择数据集', 'order_v2');

    await waitFor(() => {
      expect(screen.getByText('迁移数据集')).toBeInTheDocument();
    });
    expect(screen.getByText(/对象基础信息/)).toBeInTheDocument();
    expect(screen.getByText('确认迁移')).toBeInTheDocument();
  });

  it('fetches the new dataset schema on switch and runs same-name matching', async () => {
    const user = userEvent.setup();
    const ot = makeObjectType({ bound: 'order_v1' });
    schemaColumns.push(
      { name: 'customer_id', type: 'bigint', nullable: false },
      { name: 'order_status', type: 'varchar', nullable: true },
      { name: 'created_at', type: 'timestamp', nullable: true },
    );
    renderDialog({ objectType: ot });

    await pickFromSelect(user, '选择数据集', 'order_v2');

    await waitFor(() => {
      expect(getDatasetSchema).toHaveBeenCalledWith('order_v2');
    });

    // customerId → customer_id (same-name), createdAt → created_at (same-name).
    // status had backing_column 'status_cd' (gone in new dataset) and no
    // same-name column → unmapped.
    await waitFor(() => {
      expect(selectTriggerText('属性 customerId 的源列')).toMatch(/customer_id/);
    });
    expect(selectTriggerText('属性 createdAt 的源列')).toMatch(/created_at/);
    // status unmapped: trigger shows the placeholder "—".
    expect(selectTriggerText('属性 status 的源列')).toMatch(/—/);
  });

  it('invokes AI mapping on button click and applies high-confidence suggestions', async () => {
    const user = userEvent.setup();
    const ot = makeObjectType({ bound: 'order_v1' });
    schemaColumns.push(
      { name: 'customer_id', type: 'bigint', nullable: false },
      { name: 'order_status', type: 'varchar', nullable: true },
      { name: 'created_at', type: 'timestamp', nullable: true },
    );
    // AI matches status → order_status (semantic, which same-name missed).
    aiSuggestions.push(
      { property_api_name: 'customerId', column_name: 'customer_id', confidence: 'high' },
      { property_api_name: 'status', column_name: 'order_status', confidence: 'high' },
      { property_api_name: 'createdAt', column_name: 'created_at', confidence: 'high' },
    );
    renderDialog({ objectType: ot });

    // Switch dataset so schema loads.
    await pickFromSelect(user, '选择数据集', 'order_v2');
    await waitFor(() => expect(getDatasetSchema).toHaveBeenCalled());

    const aiBtn = screen.getByText('✨ AI 智能映射');
    await user.click(aiBtn);

    await waitFor(() => {
      expect(suggestColumnMappings).toHaveBeenCalled();
    });
    // status should now be mapped to order_status via AI.
    await waitFor(() => {
      expect(selectTriggerText('属性 status 的源列')).toMatch(/order_status/);
    });
  });

  it('shows a type-incompatibility warning for mismatched types', async () => {
    const user = userEvent.setup();
    const ot = makeObjectType({ bound: 'order_v1' });
    schemaColumns.push(
      { name: 'customer_id', type: 'boolean', nullable: false }, // incompatible with LONG
      { name: 'created_at', type: 'timestamp', nullable: true },
    );
    renderDialog({ objectType: ot });

    await pickFromSelect(user, '选择数据集', 'order_v2');
    await waitFor(() => expect(getDatasetSchema).toHaveBeenCalled());

    // customer_id (boolean) against LONG property → incompatible marker.
    // The verdict is shown as a ⚠ next to the column type, with the
    // human-readable reason in the title attribute.
    await waitFor(() => {
      const titleEl = screen.getByTitle(/类型不兼容/);
      expect(titleEl).toHaveTextContent(/boolean/);
    });
  });

  it('disables submit when any property is unmapped (migration)', async () => {
    const user = userEvent.setup();
    const ot = makeObjectType({ bound: 'order_v1' });
    // Only customer_id + created_at match by name; status stays unmapped.
    schemaColumns.push(
      { name: 'customer_id', type: 'bigint', nullable: false },
      { name: 'created_at', type: 'timestamp', nullable: true },
    );
    renderDialog({ objectType: ot });

    await pickFromSelect(user, '选择数据集', 'order_v2');
    await waitFor(() => expect(getDatasetSchema).toHaveBeenCalled());

    // status is unmapped → submit button is disabled.
    const submitBtn = screen.getByText('确认迁移');
    expect(submitBtn).toBeDisabled();
    // And an inline warning is shown.
    expect(screen.getByText(/还有 1 个属性未映射/)).toBeInTheDocument();
  });

  it('submits the mapping via linkDataset on save when all properties mapped', async () => {
    const user = userEvent.setup();
    const ot = makeObjectType({ bound: 'order_v1' });
    schemaColumns.push(
      { name: 'customer_id', type: 'bigint', nullable: false },
      { name: 'order_status', type: 'varchar', nullable: true },
      { name: 'created_at', type: 'timestamp', nullable: true },
    );
    // AI fills status → order_status so all 3 are mapped.
    aiSuggestions.push(
      { property_api_name: 'customerId', column_name: 'customer_id', confidence: 'high' },
      { property_api_name: 'status', column_name: 'order_status', confidence: 'high' },
      { property_api_name: 'createdAt', column_name: 'created_at', confidence: 'high' },
    );
    renderDialog({ objectType: ot });

    await pickFromSelect(user, '选择数据集', 'order_v2');
    await waitFor(() => expect(getDatasetSchema).toHaveBeenCalled());

    // Run AI mapping so all properties are mapped.
    await user.click(screen.getByText('✨ AI 智能映射'));
    await waitFor(() => expect(suggestColumnMappings).toHaveBeenCalled());
    await waitFor(() => {
      expect(selectTriggerText('属性 status 的源列')).toMatch(/order_status/);
    });

    // Now all mapped → submit enabled.
    const submitBtn = screen.getByText('确认迁移');
    expect(submitBtn).not.toBeDisabled();
    await user.click(submitBtn);

    await waitFor(() => {
      expect(linkDataset).toHaveBeenCalledWith(
        'Manuf',
        'Order',
        'order_v2',
        expect.arrayContaining([
          expect.objectContaining({ property_api_name: 'customerId', column_name: 'customer_id' }),
          expect.objectContaining({ property_api_name: 'status', column_name: 'order_status' }),
          expect.objectContaining({ property_api_name: 'createdAt', column_name: 'created_at' }),
        ]),
      );
    });
    expect(onSaved).toHaveBeenCalled();
    expect(onClose).toHaveBeenCalled();
  });
});
