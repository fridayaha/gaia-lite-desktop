import { describe, it, expect, vi } from 'vitest';
import { render, screen, fireEvent } from '@testing-library/react';
import { Disclosure } from '../ui/Disclosure';

describe('Disclosure (React Aria wrapper)', () => {
  it('renders the trigger button', () => {
    render(
      <Disclosure trigger="更多选项" defaultExpanded={false}>
        <p>hidden content</p>
      </Disclosure>,
    );
    expect(screen.getByText('更多选项')).toBeInTheDocument();
  });

  it('toggles the panel on click (uncontrolled)', () => {
    const onExpandedChange = vi.fn();
    render(
      <Disclosure trigger="更多" defaultExpanded={false} onExpandedChange={onExpandedChange}>
        <p>hidden content</p>
      </Disclosure>,
    );
    const trigger = screen.getByRole('button');
    expect(trigger).toHaveAttribute('aria-expanded', 'false');
    fireEvent.click(trigger);
    expect(onExpandedChange).toHaveBeenCalledWith(true);
  });
});
