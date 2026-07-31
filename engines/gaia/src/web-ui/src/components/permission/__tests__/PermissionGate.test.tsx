import { describe, it, expect } from 'vitest';
import { render, screen } from '@testing-library/react';
import { PermissionGate } from '../PermissionGate';
import type { AllowedActionsMap } from '../../../api/permission';

const decisions = (overrides: Record<string, { allowed: string[]; disabled?: Record<string, string> }> = {}): AllowedActionsMap => {
  const out: AllowedActionsMap = {};
  for (const [id, v] of Object.entries(overrides)) {
    out[id] = { allowedActions: v.allowed, disabledReasons: v.disabled ?? {} };
  }
  return out;
};

describe('PermissionGate', () => {
  it('renders children when action is allowed', () => {
    render(
      <PermissionGate action="datasource:edit" resourceId="ds-1"
        decisions={decisions({ 'ds-1': { allowed: ['datasource:edit'] } })}>
        <button>编辑</button>
      </PermissionGate>,
    );
    expect(screen.getByText('编辑')).toBeInTheDocument();
  });

  it('hides children (renders fallback) in hide mode when denied', () => {
    render(
      <PermissionGate action="datasource:delete" resourceId="ds-1"
        decisions={decisions({ 'ds-1': { allowed: [], disabled: { 'datasource:delete': '需要 Owner' } } })}
        fallback={<span>无此操作</span>}>
        <button>删除</button>
      </PermissionGate>,
    );
    expect(screen.queryByText('删除')).not.toBeInTheDocument();
    expect(screen.getByText('无此操作')).toBeInTheDocument();
  });

  it('renders nothing by default in hide mode when denied (no fallback)', () => {
    const { container } = render(
      <PermissionGate action="datasource:delete" resourceId="ds-1"
        decisions={decisions({ 'ds-1': { allowed: [], disabled: { 'datasource:delete': '无权限' } } })}>
        <button>删除</button>
      </PermissionGate>,
    );
    expect(container.querySelector('button')).not.toBeInTheDocument();
  });

  it('renders children greyed-out with reason tooltip in disable mode when denied', () => {
    render(
      <PermissionGate
        action="datasource:delete"
        resourceId="ds-1"
        mode="disable"
        decisions={decisions({
          'ds-1': { allowed: [], disabled: { 'datasource:delete': '需要 Owner 角色' } },
        })}
      >
        <button>删除</button>
      </PermissionGate>,
    );
    const btn = screen.getByText('删除');
    expect(btn).toBeInTheDocument();
    // The wrapper carries the denial reason as a title tooltip.
    const wrapper = btn.parentElement!;
    expect(wrapper.title).toBe('需要 Owner 角色');
    expect(wrapper).toHaveAttribute('aria-disabled');
  });

  it('denies when resource id not in decisions', () => {
    render(
      <PermissionGate action="datasource:edit" resourceId="unknown"
        decisions={decisions({ 'ds-1': { allowed: ['datasource:edit'] } })}>
        <button>编辑</button>
      </PermissionGate>,
    );
    expect(screen.queryByText('编辑')).not.toBeInTheDocument();
  });
});
