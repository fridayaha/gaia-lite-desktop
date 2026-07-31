/**
 * useAllowedActions — consume ship-the-decision permission decisions (design §8.2).
 *
 * The Render Gate of the three-gate model (design §8.2 / research §2.3).
 * Calls POST /authz/allowed-actions ONCE for a batch of resource ids and
 * returns a map the UI reads with `isAllowed(resourceId, action)`. This is
 * the "ship the decision" pattern: the frontend renders state from backend
 * decisions, never re-derives rules.
 *
 * Usage (list page):
 *   const ids = items.map(i => i.api_name);
 *   const { decisions, isAllowed, loading } = useAllowedActions('DATASOURCE', ids);
 *   <PermissionGate action="datasource:edit" resourceId={ds.api_name} decisions={decisions}>
 *
 * The hook dedupes + skips empty id lists. Refetches when ids change (stable
 * by sorted-join key to avoid refetch on re-order).
 */
import { useEffect, useState } from 'react';
import {
  getAllowedActions,
  type AllowedActionsMap,
  type ResourcePermission,
} from '../api/permission';
import { ApiError } from '../api/client';

const EMPTY: ResourcePermission = { allowedActions: [], disabledReasons: {} };

export interface UseAllowedActionsResult {
  /** resource_id → permission decision (empty until loaded). */
  decisions: AllowedActionsMap;
  /** True while the batch request is in flight. */
  loading: boolean;
  /** Error message if the request failed (null otherwise). */
  error: string | null;
  /** Convenience: is `action` allowed on `resourceId`? */
  isAllowed: (resourceId: string, action: string) => boolean;
  /** Convenience: why is `action` disabled on `resourceId`? (empty if allowed) */
  disabledReason: (resourceId: string, action: string) => string;
}

export function useAllowedActions(
  resourceType: string,
  resourceIds: string[],
): UseAllowedActionsResult {
  // Stable key for the id set — refetch only when the SET changes, not order.
  const key = [...resourceIds].sort().join(',');
  const [decisions, setDecisions] = useState<AllowedActionsMap>({});
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    const ids = key ? key.split(',') : [];
    if (ids.length === 0) {
      setDecisions({});
      return;
    }
    let cancelled = false;
    setLoading(true);
    setError(null);
    getAllowedActions(resourceType, ids)
      .then((map) => {
        if (!cancelled) setDecisions(map);
      })
      .catch((e) => {
        if (!cancelled) {
          setError(e instanceof ApiError ? e.detail ?? e.message : String(e));
        }
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [resourceType, key]);

  const isAllowed = (resourceId: string, action: string): boolean =>
    decisions[resourceId]?.allowedActions.includes(action) ?? false;

  const disabledReason = (resourceId: string, action: string): string =>
    decisions[resourceId]?.disabledReasons[action] ?? '';

  return { decisions, loading, error, isAllowed, disabledReason };
}

export { EMPTY as NO_PERMISSION };
