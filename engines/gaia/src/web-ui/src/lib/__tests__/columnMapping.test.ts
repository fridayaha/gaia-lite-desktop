import { describe, it, expect } from 'vitest';
import {
  normalizeName,
  checkTypeCompatibility,
  autoMatchByColumnName,
  mergeMappings,
} from '../columnMapping';
import type { ColumnMappingSuggestion } from '../../api/ai';

describe('normalizeName', () => {
  it('lowercases and strips non-alphanumeric', () => {
    expect(normalizeName('customerName')).toBe('customername');
    expect(normalizeName('customer_name')).toBe('customername');
    expect(normalizeName('CustomerName')).toBe('customername');
    expect(normalizeName('CUSTOMER-NAME')).toBe('customername');
  });
  it('collapses snake/camel/Pascal to the same key', () => {
    expect(normalizeName('orderStatus')).toBe(normalizeName('order_status'));
    expect(normalizeName('OrderStatus')).toBe(normalizeName('order_status'));
  });
  it('returns empty for non-alphanumeric input', () => {
    expect(normalizeName('---')).toBe('');
    expect(normalizeName('')).toBe('');
  });
});

describe('checkTypeCompatibility', () => {
  it('exact when property type equals mapped column type', () => {
    expect(checkTypeCompatibility('STRING', 'varchar')).toBe('exact');
    expect(checkTypeCompatibility('LONG', 'bigint')).toBe('exact');
    expect(checkTypeCompatibility('BOOLEAN', 'boolean')).toBe('exact');
  });
  it('compatible for widening numeric conversions (no loss)', () => {
    expect(checkTypeCompatibility('INTEGER', 'bigint')).toBe('compatible');
    expect(checkTypeCompatibility('SHORT', 'int')).toBe('compatible');
  });
  it('warn for narrowing numeric conversions (lossy)', () => {
    expect(checkTypeCompatibility('LONG', 'int')).toBe('warn');
    expect(checkTypeCompatibility('DOUBLE', 'int')).toBe('warn');
  });
  it('exact for STRING property against any unknown/complex column type (both degrade to STRING)', () => {
    // array<int> degrades to STRING via trinoTypeToDataType
    expect(checkTypeCompatibility('STRING', 'array<int>')).toBe('exact');
    expect(checkTypeCompatibility('STRING', 'row(x int)')).toBe('exact');
  });
  it('warn when column degrades to STRING but property is numeric', () => {
    expect(checkTypeCompatibility('LONG', 'array<int>')).toBe('warn');
  });
  it('compatible for DATE ↔ TIMESTAMP', () => {
    expect(checkTypeCompatibility('DATE', 'timestamp')).toBe('compatible');
    expect(checkTypeCompatibility('TIMESTAMP', 'date')).toBe('compatible');
  });
  it('incompatible for clearly mismatched types', () => {
    expect(checkTypeCompatibility('BOOLEAN', 'bigint')).toBe('incompatible');
    expect(checkTypeCompatibility('BOOLEAN', 'varchar')).toBe('warn'); // STRING col → warn
    expect(checkTypeCompatibility('INTEGER', 'boolean')).toBe('incompatible');
  });
});

describe('autoMatchByColumnName', () => {
  const columns = [
    { name: 'customer_id' },
    { name: 'customer_name' },
    { name: 'created_at' },
  ];
  it('matches by normalized api_name (camelCase ↔ snake_case)', () => {
    const props = [{ api_name: 'customerId' }, { api_name: 'customerName' }];
    const m = autoMatchByColumnName(props, columns);
    expect(m['customerId']).toBe('customer_id');
    expect(m['customerName']).toBe('customer_name');
  });
  it('preserves an existing source_column when it still exists in the new dataset', () => {
    const props = [{ api_name: 'weirdApi', source_column: 'created_at' }];
    const m = autoMatchByColumnName(props, columns);
    expect(m['weirdApi']).toBe('created_at');
  });
  it('falls back to name match when existing source_column is gone', () => {
    const props = [{ api_name: 'customerId', source_column: 'old_pk' }];
    const m = autoMatchByColumnName(props, columns);
    expect(m['customerId']).toBe('customer_id');
  });
  it('leaves unmatched properties out of the result', () => {
    const props = [{ api_name: 'noMatchingColumn' }];
    const m = autoMatchByColumnName(props, columns);
    expect(m['noMatchingColumn']).toBeUndefined();
  });
  it('does not match when normalized name is empty', () => {
    const props = [{ api_name: '中文' }];
    const m = autoMatchByColumnName(props, columns);
    expect(m['中文']).toBeUndefined();
  });
});

describe('mergeMappings', () => {
  const properties = [
    { api_name: 'customerId' },
    { api_name: 'status' },
    { api_name: 'unmatched' },
  ];
  const columns = [
    { name: 'customer_id' },
    { name: 'order_status' },
    { name: 'status' },
  ];

  it('AI high-confidence wins over same-name', () => {
    const ai: ColumnMappingSuggestion[] = [
      { property_api_name: 'customerId', column_name: 'customer_id', confidence: 'high' },
      { property_api_name: 'status', column_name: 'order_status', confidence: 'high' },
      { property_api_name: 'unmatched', column_name: '', confidence: 'low' },
    ];
    const merged = mergeMappings(properties, columns, ai);
    expect(merged.find((m) => m.property_api_name === 'customerId')).toMatchObject({
      column_name: 'customer_id',
      source: 'ai-high',
    });
    expect(merged.find((m) => m.property_api_name === 'status')).toMatchObject({
      column_name: 'order_status',
      source: 'ai-high',
    });
  });

  it('falls back to same-name match when AI confidence is low/empty', () => {
    const ai: ColumnMappingSuggestion[] = [
      { property_api_name: 'customerId', column_name: '', confidence: 'low' },
      { property_api_name: 'status', column_name: '', confidence: 'low' },
      { property_api_name: 'unmatched', column_name: '', confidence: 'low' },
    ];
    const merged = mergeMappings(properties, columns, ai);
    // customerId matches customer_id by name; status matches status by name.
    expect(merged.find((m) => m.property_api_name === 'customerId')?.column_name).toBe('customer_id');
    expect(merged.find((m) => m.property_api_name === 'customerId')?.source).toBe('same-name');
    expect(merged.find((m) => m.property_api_name === 'status')?.column_name).toBe('status');
    // unmatched has no same-name hit → empty.
    expect(merged.find((m) => m.property_api_name === 'unmatched')?.column_name).toBe('');
    expect(merged.find((m) => m.property_api_name === 'unmatched')?.source).toBe('none');
  });

  it('drops AI-hallucinated column names (not in dataset)', () => {
    const ai: ColumnMappingSuggestion[] = [
      { property_api_name: 'customerId', column_name: 'this_column_does_not_exist', confidence: 'high' },
      { property_api_name: 'status', column_name: '', confidence: 'low' },
      { property_api_name: 'unmatched', column_name: '', confidence: 'low' },
    ];
    const merged = mergeMappings(properties, columns, ai);
    // Hallucination dropped → falls back to same-name match.
    expect(merged.find((m) => m.property_api_name === 'customerId')?.column_name).toBe('customer_id');
    expect(merged.find((m) => m.property_api_name === 'customerId')?.source).toBe('same-name');
  });

  it('returns same-name-only result when AI suggestions are empty', () => {
    const merged = mergeMappings(properties, columns, []);
    expect(merged.find((m) => m.property_api_name === 'customerId')?.source).toBe('same-name');
    expect(merged.find((m) => m.property_api_name === 'unmatched')?.source).toBe('none');
  });

  it('guarantees one entry per input property', () => {
    const merged = mergeMappings(properties, columns, []);
    expect(merged).toHaveLength(3);
    expect(merged.map((m) => m.property_api_name).sort()).toEqual(
      ['customerId', 'status', 'unmatched'],
    );
  });
});
