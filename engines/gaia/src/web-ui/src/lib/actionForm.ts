/**
 * Action form helpers (P1, ADR-011).
 *
 * Pure functions for rendering Action parameter forms: value coercion,
 * parameter-definition extraction, and initial-value stringification.
 * Extracted from ExecuteActionDialog so they can be unit-tested in isolation.
 */

import type { ActionParameterDef } from '../types';

/**
 * Extract the parameter definitions from an ActionTypeRecord.
 *
 * Parameters live under `parameters.parameters` (per backend schema).
 */
export function extractParamDefs(action: {
  parameters: Record<string, unknown>;
}): ActionParameterDef[] {
  const params = action.parameters as { parameters?: ActionParameterDef[] };
  return params?.parameters ?? [];
}

/**
 * Stringify initial param values for form inputs.
 */
export function stringifyParams(p: Record<string, unknown>): Record<string, string> {
  const out: Record<string, string> = {};
  for (const [k, v] of Object.entries(p)) {
    out[k] = v == null ? '' : String(v);
  }
  return out;
}

/**
 * Coerce a string input to the declared datatype for the API payload.
 *
 * Returns null for empty strings (so the backend can apply defaults / required
 * validation). Throws nothing — invalid numerics become NaN which the backend
 * validator rejects with a clear type-mismatch error.
 */
export function coerceValue(raw: string | undefined, dataType: string): unknown {
  if (raw === undefined || raw === '') return null;
  const t = dataType.toUpperCase();
  if (t === 'INTEGER' || t === 'LONG' || t === 'SHORT') return Number.parseInt(raw, 10);
  if (t === 'FLOAT' || t === 'DOUBLE' || t === 'DECIMAL') return Number.parseFloat(raw);
  if (t === 'BOOLEAN') return raw.toLowerCase() === 'true';
  return raw;
}

/**
 * Decide which HTML input control to render for a parameter.
 *
 * P1 (ADR-011): replaces the previous "everything is a text input" approach.
 */
export type ParameterControlKind =
  'text' | 'checkbox' | 'date' | 'datetime-local' | 'select' | 'object-ref';

export function controlKindFor(def: ActionParameterDef): ParameterControlKind {
  if (def.enum_values && def.enum_values.length > 0) return 'select';
  if (def.object_type_ref) return 'object-ref';
  const t = def.data_type.toUpperCase();
  if (t === 'BOOLEAN') return 'checkbox';
  if (t === 'DATE') return 'date';
  if (t === 'TIMESTAMP') return 'datetime-local';
  return 'text';
}
