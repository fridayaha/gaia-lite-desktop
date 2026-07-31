import { describe, it, expect } from 'vitest';
import { render, screen } from '@testing-library/react';
import { DatePicker } from '../ui/DatePicker';

describe('DatePicker (React Aria wrapper)', () => {
  it('parses an ISO value into displayed date segments', () => {
    render(<DatePicker value="2026-06-28" aria-label="d" onChange={() => {}} />);
    // Year/day segments render their literal text.
    expect(screen.getByText('2026')).toBeInTheDocument();
    expect(screen.getByText('28')).toBeInTheDocument();
  });

  it('renders without a value (empty field)', () => {
    const { container } = render(<DatePicker value="" aria-label="d" onChange={() => {}} />);
    // No crash; the field renders.
    expect(container.firstChild).not.toBeNull();
  });
});
