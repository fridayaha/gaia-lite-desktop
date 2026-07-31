import { describe, it, expect } from 'vitest';
import { render, screen } from '@testing-library/react';
import { Select, SelectOption } from '../ui/Select';

describe('Select (React Aria wrapper)', () => {
  it('renders the trigger button and the selected option label', () => {
    render(
      <Select value="b" onChange={() => {}} aria-label="pick">
        <SelectOption label="Alpha" value="a" />
        <SelectOption label="Beta" value="b" />
      </Select>,
    );
    // The trigger is a button with aria-haspopup="listbox".
    const trigger = screen.getByRole('button', { name: /pick/ });
    expect(trigger).toHaveAttribute('aria-haspopup', 'listbox');
    // SelectValue shows the selected item's label inside the trigger.
    expect(trigger).toHaveTextContent('Beta');
  });

  it('shows placeholder text when no value is selected', () => {
    render(
      <Select placeholder="— 选择 —" onChange={() => {}} aria-label="pick">
        <SelectOption label="Alpha" value="a" />
      </Select>,
    );
    expect(screen.getByRole('button', { name: /pick/ })).toHaveTextContent('— 选择 —');
  });

  it('renders the trigger for a multi-option select', () => {
    render(
      <Select value="a" onChange={() => {}} aria-label="pick">
        <SelectOption label="Alpha" value="a" />
        <SelectOption label="Beta" value="b" />
        <SelectOption label="Gamma" value="c" />
      </Select>,
    );
    // The trigger shows the selected value.
    expect(screen.getByRole('button', { name: /pick/ })).toHaveTextContent('Alpha');
  });

  it('applies the disabled state to the trigger', () => {
    render(
      <Select value="a" disabled onChange={() => {}} aria-label="pick">
        <SelectOption label="Alpha" value="a" />
      </Select>,
    );
    const trigger = screen.getByRole('button', { name: /pick/ });
    // React Aria marks a disabled Select's button as disabled (HTML) or
    // aria-disabled; accept either.
    const isDisabled =
      trigger.hasAttribute('disabled') || trigger.getAttribute('aria-disabled') === 'true';
    expect(isDisabled).toBe(true);
  });
});
