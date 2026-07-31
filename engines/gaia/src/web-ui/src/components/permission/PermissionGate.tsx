/**
 * PermissionGate — declarative Render Gate for permission-aware UI (design §8.2).
 *
 * The first of three gates (Render / Data / Backend). Wraps any control or
 * section and hides or disables it based on backend-shipped permission
 * decisions. The frontend never re-derives rules — it reads
 * `useAllowedActions` decisions that the backend shipped (ship-the-decision).
 *
 * Two modes:
 *   - "hide" (default): renders `fallback` (or nothing) when disallowed.
 *     Use for navigation / menus — the user never感知到无权资源的存在
 *     (不可见即安全).
 *   - "disable": renders children greyed-out + a tooltip with the denial
 *     reason. Use for mutative actions on a visible resource — the user
 *     knows the action exists but learns why they can't perform it.
 *
 * Usage:
 *   const { decisions } = useAllowedActions('DATASOURCE', [ds.api_name]);
 *   <PermissionGate action="datasource:edit" resourceId={ds.api_name} decisions={decisions} mode="disable">
 *     <Button onPress={handleEdit}>编辑</Button>
 *   </PermissionGate>
 *
 *   <PermissionGate action="space:admin" resourceId={space.id} decisions={decisions} mode="hide">
 *     <NavItem href="/settings/spaces">Space 设置</NavItem>
 *   </PermissionGate>
 *
 * Decisions are passed in (not fetched here) so a single useAllowedActions
 * call at the page level serves many gates — no per-gate requests.
 */
import type { ReactNode } from 'react';
import type { AllowedActionsMap } from '../../api/permission';
import { cn } from '../../lib/cn';

interface PermissionGateProps {
  /** The action to check, e.g. "datasource:edit" / "object:view". */
  action: string;
  /** The resource id (api_name) the action applies to. */
  resourceId: string;
  /** Permission decisions from useAllowedActions (ship-the-decision). */
  decisions: AllowedActionsMap;
  /** "hide" = render fallback (default); "disable" = grey out + reason tooltip. */
  mode?: 'hide' | 'disable';
  /** Content to render when allowed (both modes) or fallback when disallowed (hide mode). */
  children: ReactNode;
  /** Shown in place of children when disallowed in "hide" mode. */
  fallback?: ReactNode;
}

export function PermissionGate({
  action,
  resourceId,
  decisions,
  mode = 'hide',
  children,
  fallback = null,
}: PermissionGateProps) {
  const perm = decisions[resourceId];
  const allowed = perm?.allowedActions.includes(action) ?? false;

  if (allowed) return <>{children}</>;

  if (mode === 'disable') {
    const reason = perm?.disabledReasons[action] || '无权限';
    // title attribute = lightweight tooltip (no extra dependency). Children
    // are rendered but non-interactive + dimmed, so the user perceives the
    // action exists but is gated.
    return (
      <div
        title={reason}
        aria-disabled
        className={cn('inline-flex opacity-50 pointer-events-none cursor-not-allowed')}
      >
        {children}
      </div>
    );
  }

  return <>{fallback}</>;
}
