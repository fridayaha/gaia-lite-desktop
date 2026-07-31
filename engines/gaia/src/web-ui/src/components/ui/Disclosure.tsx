/**
 * Project-level Disclosure (accordion) wrapper around React Aria Components
 * (ADR-013 Phase 5).
 *
 * A collapsible panel with proper ARIA (aria-expanded, aria-controls), keyboard
 * activation, and focus management. Use for property rows / config sections
 * that expand/collapse.
 *
 * Controlled or uncontrolled: pass `isExpanded`/`onExpandedChange` for
 * controlled, or `defaultExpanded` for uncontrolled.
 *
 * See ADR-013.
 */
import {
  Disclosure as AriaDisclosure,
  DisclosurePanel,
  Button,
  type DisclosureProps,
} from 'react-aria-components';
import { cn } from '../../lib/cn';

interface UIDisclosureProps extends Omit<DisclosureProps, 'children'> {
  /** The trigger label (rendered inside the toggle Button). */
  trigger: React.ReactNode;
  /** The collapsible content. */
  children: React.ReactNode;
  /** Class on the trigger button. */
  triggerClassName?: string;
  /** Class on the panel. */
  panelClassName?: string;
  /** Controlled expanded state. */
  isExpanded?: boolean;
  /** Default expanded (uncontrolled). */
  defaultExpanded?: boolean;
  /** Called when expansion changes (controlled). */
  onExpandedChange?: (isExpanded: boolean) => void;
}

export function Disclosure({
  trigger,
  children,
  triggerClassName,
  panelClassName,
  ...rest
}: UIDisclosureProps) {
  return (
    <AriaDisclosure {...rest} className={cn('flex flex-col', rest.className)}>
      {({ isExpanded }) => (
        <>
          <Button
            slot="trigger"
            className={cn(
              'flex items-center justify-between gap-2 rounded-md px-3 py-2 text-sm font-medium text-text outline-none data-[hovered]:bg-white/[0.03] data-[focus-visible]:bg-[var(--accent-bg)]',
              triggerClassName,
            )}
          >
            <span className="flex-1 text-left">{trigger}</span>
            <span
              className={cn('text-text-muted transition-transform', isExpanded && 'rotate-90')}
              aria-hidden="true"
            >
              ▶
            </span>
          </Button>
          <DisclosurePanel className={cn('px-3 py-2', panelClassName)}>{children}</DisclosurePanel>
        </>
      )}
    </AriaDisclosure>
  );
}
