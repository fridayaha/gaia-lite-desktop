/** Trino/Iceberg type string → Gaia DataType enum.
 *
 * Aligns with the backend's `_iceberg_type_from_str` mapping. Used by the
 * wizard's "从数据集生成属性" action (F2) to pick a sensible DataType for
 * each dataset column. Complex/unknown types degrade to STRING so the user
 * can always proceed and refine manually.
 *
 * See docs/design/dataset-ontology-binding.md §4.4 (F2).
 */
import type { DataType } from '../types';

const TYPE_MAP: Record<string, DataType> = {
  // strings
  string: 'STRING',
  varchar: 'STRING',
  char: 'STRING',
  text: 'STRING',
  // booleans
  boolean: 'BOOLEAN',
  bool: 'BOOLEAN',
  // integers
  int: 'INTEGER',
  integer: 'INTEGER',
  // shorts
  smallint: 'SHORT',
  short: 'SHORT',
  tinyint: 'SHORT',
  byte: 'BYTE',
  // longs
  bigint: 'LONG',
  long: 'LONG',
  // floats
  float: 'FLOAT',
  real: 'FLOAT',
  // doubles
  double: 'DOUBLE',
  // decimal
  decimal: 'DECIMAL',
  numeric: 'DECIMAL',
  // date/time
  date: 'DATE',
  timestamp: 'TIMESTAMP',
  datetime: 'TIMESTAMP',
};

/**
 * Convert a Trino/Iceberg/SQL type string to a Gaia DataType.
 *
 * Strips parenthesised parameters (e.g. `varchar(255)` → `varchar`,
 * `decimal(10,2)` → `decimal`) before lookup. Unknown / complex types
 * (arrays, structs, maps, geo) fall back to STRING.
 */
export function trinoTypeToDataType(trinoType: string): DataType {
  if (!trinoType) return 'STRING';
  const base = trinoType
    .toLowerCase()
    .replace(/\(.*\)/, '')
    .trim();
  return TYPE_MAP[base] ?? 'STRING';
}
