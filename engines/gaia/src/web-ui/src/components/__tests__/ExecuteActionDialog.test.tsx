import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import { ExecuteActionDialog } from '../ExecuteActionDialog';
import type { ActionTypeRecord, ActionExecutionResult } from '../../types';

// Mock the api/client executeAction
vi.mock('../../api/client', () => ({
  executeAction: vi.fn(),
}));

import { executeAction } from '../../api/client';

const mockAction: ActionTypeRecord = {
  id: 'at1',
  ontology_id: 'o1',
  api_name: 'ship_order',
  display_name: 'Ship Order',
  description: 'Ship an order',
  affected_object_type_id: 'ot1',
  parameters: {
    parameters: [
      { api_name: 'status', display_name: 'Status', data_type: 'STRING', required: true },
      {
        api_name: 'priority',
        display_name: 'Priority',
        data_type: 'STRING',
        enum_values: ['low', 'high'],
        required: false,
      },
    ],
  },
  rules: {},
  submission_criteria: {},
  status: 'ACTIVE',
  created_at: '',
  updated_at: '',
};

describe('ExecuteActionDialog', () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it('renders parameter fields from action type', () => {
    render(
      <ExecuteActionDialog
        open
        onClose={() => {}}
        ontology="hr"
        objectType="order"
        action={mockAction}
      />,
    );
    expect(screen.getByText(/Status/)).toBeInTheDocument();
    expect(screen.getByText(/Priority/)).toBeInTheDocument();
  });

  it('renders a select for enum parameter', () => {
    render(
      <ExecuteActionDialog
        open
        onClose={() => {}}
        ontology="hr"
        objectType="order"
        action={mockAction}
      />,
    );
    // React Aria Select renders a button trigger with a listbox popup
    // (the Priority enum param).
    const triggers = screen.getAllByRole('button');
    expect(triggers.some((b) => b.getAttribute('aria-haspopup') === 'listbox')).toBe(true);
  });

  it('submits with coerced payload and idempotency key', async () => {
    const result: ActionExecutionResult = {
      status: 'applied',
      action_id: 'act-1',
      affected_objects: {},
      mutations: [],
      validation_errors: [],
    };
    vi.mocked(executeAction).mockResolvedValueOnce(result);

    render(
      <ExecuteActionDialog
        open
        onClose={() => {}}
        ontology="hr"
        objectType="order"
        action={mockAction}
      />,
    );
    fireEvent.change(screen.getAllByRole('textbox')[0], {
      target: { value: 'shipped' },
    });
    fireEvent.click(screen.getByText('执行'));

    await waitFor(() => {
      expect(executeAction).toHaveBeenCalledWith(
        'hr',
        'order',
        'ship_order',
        expect.objectContaining({
          parameters: expect.objectContaining({ status: 'shipped' }),
          idempotency_key: expect.stringContaining('ship_order-'),
        }),
      );
    });
  });

  it('shows applied status on success', async () => {
    const result: ActionExecutionResult = {
      status: 'applied',
      action_id: 'act-1',
      affected_objects: { 'order-1': 2 },
      mutations: [],
      validation_errors: [],
    };
    vi.mocked(executeAction).mockResolvedValueOnce(result);

    render(
      <ExecuteActionDialog
        open
        onClose={() => {}}
        ontology="hr"
        objectType="order"
        action={mockAction}
      />,
    );
    fireEvent.click(screen.getByText('执行'));

    await waitFor(() => {
      expect(screen.getByText(/已生效/)).toBeInTheDocument();
    });
    expect(screen.getByText(/read-your-writes/)).toBeInTheDocument();
  });

  it('shows validation_failed errors', async () => {
    const result: ActionExecutionResult = {
      status: 'validation_failed',
      action_id: '',
      affected_objects: {},
      mutations: [],
      validation_errors: ['Missing required parameter: status'],
    };
    vi.mocked(executeAction).mockResolvedValueOnce(result);

    render(
      <ExecuteActionDialog
        open
        onClose={() => {}}
        ontology="hr"
        objectType="order"
        action={mockAction}
      />,
    );
    fireEvent.click(screen.getByText('执行'));

    await waitFor(() => {
      expect(screen.getByText('Missing required parameter: status')).toBeInTheDocument();
    });
  });

  it('invokes onApplied callback when status is applied', async () => {
    const onApplied = vi.fn();
    const result: ActionExecutionResult = {
      status: 'applied',
      action_id: 'act-1',
      affected_objects: {},
      mutations: [],
      validation_errors: [],
    };
    vi.mocked(executeAction).mockResolvedValueOnce(result);

    render(
      <ExecuteActionDialog
        open
        onClose={() => {}}
        ontology="hr"
        objectType="order"
        action={mockAction}
        onApplied={onApplied}
      />,
    );
    fireEvent.click(screen.getByText('执行'));

    await waitFor(() => {
      expect(onApplied).toHaveBeenCalledTimes(1);
    });
  });
});
