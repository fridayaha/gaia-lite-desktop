/** Dataset remapping helpers — shared by CreateObjectWizard (first binding)
 *  and DatasetLinkDialog (migrate / rebind).
 *
 *  Three concerns:
 *  1. Type compatibility: judge whether a property's DataType is compatible
 *     with a dataset column's physical (Trino/Iceberg) type, so the UI can
 *     warn (not block) on mismatches. "数据类型尽量不变" is a guideline, not
 *     a hard rule — the user may intentionally remap.
 *  2. Same-name auto-match: deterministic property ↔ column matching by
 *     normalized name (snake_case ↔ camelCase ↔ PascalCase), the zero-cost
 *     fallback when the LLM is unavailable or unnecessary.
 *  3. sanitizeMappings: strip AI-hallucinated column names so only real
 *     columns reach the backend.
 */
import type { DataType } from '../types';
import { trinoTypeToDataType } from './typeMapping';
import type { ColumnMappingSuggestion } from '../api/ai';

/** Compatibility verdict for a property's type vs a column's type. */
export type TypeCompat = 'exact' | 'compatible' | 'warn' | 'incompatible';

/** Normalize an identifier for same-name matching.
 *
 *  Strips to alphanumeric, lowercases, so `customerName`, `customer_name`,
 *  `CustomerName`, `CUSTOMER_NAME` all collide. Used by autoMatchByColumnName
 *  and as the deterministic fallback behind AI mapping. */
export function normalizeName(name: string): string {
  return name.toLowerCase().replace(/[^a-z0-9]/g, '');
}

/** Judge whether a Gaia DataType is compatible with a Trino/Iceberg type.
 *
 *  - 'exact': the column type maps directly to the property type.
 *  - 'compatible': widening / same-family (e.g. INTEGER → LONG) — safe.
 *  - 'warn': lossy but plausible (e.g. DOUBLE → INTEGER) — surface to user.
 *  - 'incompatible': clearly wrong (e.g. STRING → BOOLEAN).
 *
 *  Conservative: unknown column types fall back to 'compatible' (the user
 *  decided the mapping; we don't block on unrecognized physical types). */
export function checkTypeCompatibility(propType: DataType, columnType: string): TypeCompat {
  const colDataType = trinoTypeToDataType(columnType);
  if (propType === colDataType) return 'exact';

  // Same numeric family, widening direction (no loss).
  const numericWide: Record<string, number> = { SHORT: 1, INTEGER: 2, LONG: 3, FLOAT: 4, DOUBLE: 5, DECIMAL: 5 };
  if (propType in numericWide && colDataType in numericWide) {
    return numericWide[propType] <= numericWide[colDataType] ? 'compatible' : 'warn';
  }
  // STRING is a catch-all bucket in trinoTypeToDataType, so a property STRING
  // against any complex/unknown column type is treated as compatible (the
  // column degraded to STRING anyway).
  if (propType === 'STRING') return 'compatible';
  if (colDataType === 'STRING') return 'warn';

  // DATE vs TIMESTAMP — close enough, flag as compatible.
  if (
    (propType === 'DATE' && colDataType === 'TIMESTAMP') ||
    (propType === 'TIMESTAMP' && colDataType === 'DATE')
  ) {
    return 'compatible';
  }

  return 'incompatible';
}

/** Deterministic property → column matching by normalized name.
 *
 *  Returns a map of property_api_name → column_name for properties whose
 *  normalized name matches exactly one column. Columns matched to multiple
 *  properties are NOT assigned (ambiguous — left for the user / AI). This is
 *  the zero-cost fallback and the pre-fill used right after switching dataset.
 *
 *  `properties` items need api_name + (optionally) source_column — the latter
 *  is checked too so a property already bound to a column that still exists
 *  in the new dataset keeps its mapping. */
export function autoMatchByColumnName(
  properties: { api_name: string; source_column?: string | null }[],
  columns: { name: string }[],
): Record<string, string> {
  // Build normalized column name → actual column name (first occurrence wins).
  const colByNorm = new Map<string, string>();
  for (const c of columns) {
    const norm = normalizeName(c.name);
    if (norm && !colByNorm.has(norm)) colByNorm.set(norm, c.name);
  }
  const result: Record<string, string> = {};
  for (const p of properties) {
    // 1. Prefer the existing source_column if it still exists in the new dataset.
    if (p.source_column && columns.some((c) => c.name === p.source_column)) {
      result[p.api_name] = p.source_column;
      continue;
    }
    // 2. Match by normalized property api_name.
    const norm = normalizeName(p.api_name);
    const hit = norm ? colByNorm.get(norm) : undefined;
    if (hit) result[p.api_name] = hit;
  }
  return result;
}

/** Merge deterministic same-name matches with AI suggestions.
 *
 *  Strategy: AI suggestions win when confidence is high/medium; deterministic
 *  same-name match fills the gaps (AI low/empty or AI unavailable). Returns
 *  the final property_api_name → column_name map plus a per-property
 *  confidence/source tag for UI display.
 *
 *  `aiSuggestions` may be empty (AI failed) — the result is then purely
 *  deterministic. `columns` is used to sanitize AI column names. */
export interface MergedMapping {
  property_api_name: string;
  column_name: string;
  /** Where the mapping came from — drives UI badges. */
  source: 'ai-high' | 'ai-medium' | 'same-name' | 'none';
  /** Original AI confidence, preserved for the 'none' case (AI said low/empty). */
  ai_confidence: 'high' | 'medium' | 'low' | null;
}

export function mergeMappings(
  properties: { api_name: string; source_column?: string | null }[],
  columns: { name: string }[],
  aiSuggestions: ColumnMappingSuggestion[],
): MergedMapping[] {
  const columnNames = new Set(columns.map((c) => c.name));
  const autoMatched = autoMatchByColumnName(properties, columns);
  const aiByProp = new Map(aiSuggestions.map((s) => [s.property_api_name, s]));

  return properties.map((p) => {
    const ai = aiByProp.get(p.api_name);
    // Sanitize AI column name: must be a real column (drop hallucinations).
    const aiCol = ai?.column_name && columnNames.has(ai.column_name) ? ai.column_name : '';

    if (aiCol && (ai?.confidence === 'high' || ai?.confidence === 'medium')) {
      return {
        property_api_name: p.api_name,
        column_name: aiCol,
        source: ai.confidence === 'high' ? 'ai-high' : 'ai-medium',
        ai_confidence: ai.confidence,
      };
    }
    const detCol = autoMatched[p.api_name];
    if (detCol) {
      return { property_api_name: p.api_name, column_name: detCol, source: 'same-name', ai_confidence: ai?.confidence ?? null };
    }
    return {
      property_api_name: p.api_name,
      column_name: aiCol, // keep a sanitized low-confidence AI guess if present
      source: 'none',
      ai_confidence: ai?.confidence ?? null,
    };
  });
}
