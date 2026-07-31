import { useId } from 'react';

/**
 * Returns a stable id for form element label-input association.
 * Uses React 19's useId under the hood.
 *
 * Usage:
 *   const id = useFormId('displayName');
 *   <label htmlFor={id}>Display Name</label>
 *   <input id={id} ... />
 */
export function useFormId(name: string): string {
  const reactId = useId();
  return `${name}-${reactId}`;
}

/**
 * Returns `{ htmlFor, id }` for a single field, plus a generated `fieldId`.
 * Convenience wrapper around useFormId.
 *
 * Usage:
 *   const f = useFieldId('api_name');
 *   <label htmlFor={f.htmlFor}>API Name</label>
 *   <input id={f.id} ... />
 */
export function useFieldId(name: string) {
  const id = useFormId(name);
  return { id, htmlFor: id, fieldId: id };
}
