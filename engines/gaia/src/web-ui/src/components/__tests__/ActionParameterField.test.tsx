import { describe, it, expect } from 'vitest';
import { render, screen } from '@testing-library/react';
import { ActionParameterField } from '../ActionParameterField';
import type { ActionParameterDef } from '../../types';

describe('ActionParameterField', () => {
  it('renders a checkbox for BOOLEAN', () => {
    const def: ActionParameterDef = { api_name: 'flag', data_type: 'BOOLEAN' };
    render(<ActionParameterField def={def} value="false" onChange={() => {}} />);
    expect(screen.getByRole('checkbox')).toBeInTheDocument();
  });

  it('renders a select for enum_values', () => {
    const def: ActionParameterDef = {
      api_name: 'priority',
      data_type: 'STRING',
      enum_values: ['low', 'high'],
    };
    render(<ActionParameterField def={def} value="" onChange={() => {}} />);
    // React Aria Select renders a button trigger with a listbox popup.
    const trigger = screen.getByRole('button');
    expect(trigger).toHaveAttribute('aria-haspopup', 'listbox');
  });

  it('renders a date input for DATE', () => {
    const def: ActionParameterDef = { api_name: 'd', data_type: 'DATE' };
    const { container } = render(<ActionParameterField def={def} value="" onChange={() => {}} />);
    const input = container.querySelector('input[type="date"]');
    expect(input).not.toBeNull();
  });

  it('renders a text input by default', () => {
    const def: ActionParameterDef = { api_name: 'name', data_type: 'STRING' };
    const { container } = render(<ActionParameterField def={def} value="" onChange={() => {}} />);
    const input = container.querySelector('input[type="text"]');
    expect(input).not.toBeNull();
  });

  it('disables input when readonly', () => {
    const def: ActionParameterDef = { api_name: 'x', data_type: 'STRING', readonly: true };
    const { container } = render(<ActionParameterField def={def} value="" onChange={() => {}} />);
    const input = container.querySelector('input') as HTMLInputElement;
    expect(input.disabled).toBe(true);
  });

  it('shows required marker for required params', () => {
    const def: ActionParameterDef = {
      api_name: 'status',
      data_type: 'STRING',
      required: true,
      display_name: 'Status',
    };
    render(<ActionParameterField def={def} value="" onChange={() => {}} />);
    expect(screen.getByText(/Status/)).toBeInTheDocument();
    expect(screen.getByText(/\*/)).toBeInTheDocument();
  });
});
