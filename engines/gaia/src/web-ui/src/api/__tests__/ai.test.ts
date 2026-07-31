import { describe, it, expect } from 'vitest';
import { parseMappingSuggestions, type MappingPropertyInput } from '../ai';

const props: MappingPropertyInput[] = [
  { api_name: 'customerId', display_name: '客户ID', data_type: 'LONG' },
  { api_name: 'status', display_name: '状态', data_type: 'STRING' },
  { api_name: 'createdAt', display_name: '创建时间', data_type: 'TIMESTAMP' },
];

describe('parseMappingSuggestions', () => {
  it('parses a clean JSON array', () => {
    const raw = JSON.stringify([
      { property_api_name: 'customerId', column_name: 'customer_id', confidence: 'high' },
      { property_api_name: 'status', column_name: 'order_status', confidence: 'medium' },
      { property_api_name: 'createdAt', column_name: 'created_at', confidence: 'high' },
    ]);
    const result = parseMappingSuggestions(raw, props);
    expect(result).toHaveLength(3);
    expect(result[0]).toMatchObject({ property_api_name: 'customerId', column_name: 'customer_id', confidence: 'high' });
    expect(result[1].confidence).toBe('medium');
  });

  it('strips markdown fences and surrounding prose', () => {
    const raw =
      '好的，这是映射结果：\n```json\n' +
      JSON.stringify([
        { property_api_name: 'customerId', column_name: 'customer_id', confidence: 'high' },
      ]) +
      '\n```\n希望对你有帮助。';
    const result = parseMappingSuggestions(raw, props);
    expect(result).toHaveLength(3);
    expect(result[0].column_name).toBe('customer_id');
  });

  it('backfills missing properties as low/empty', () => {
    // LLM only returned one of three properties.
    const raw = JSON.stringify([
      { property_api_name: 'customerId', column_name: 'customer_id', confidence: 'high' },
    ]);
    const result = parseMappingSuggestions(raw, props);
    expect(result).toHaveLength(3);
    expect(result[1]).toMatchObject({ property_api_name: 'status', column_name: '', confidence: 'low' });
    expect(result[2]).toMatchObject({ property_api_name: 'createdAt', column_name: '', confidence: 'low' });
  });

  it('normalizes unknown confidence values to low', () => {
    const raw = JSON.stringify([
      { property_api_name: 'customerId', column_name: 'customer_id', confidence: 'very-sure' },
    ]);
    const result = parseMappingSuggestions(raw, props);
    expect(result[0].confidence).toBe('low');
  });

  it('returns all-low/empty on unparseable output', () => {
    const raw = '抱歉，我无法完成这个任务。';
    const result = parseMappingSuggestions(raw, props);
    expect(result).toHaveLength(3);
    expect(result.every((r) => r.column_name === '' && r.confidence === 'low')).toBe(true);
  });

  it('returns all-low/empty when JSON is not an array', () => {
    const raw = JSON.stringify({ not: 'an array' });
    const result = parseMappingSuggestions(raw, props);
    expect(result).toHaveLength(3);
    expect(result.every((r) => r.column_name === '')).toBe(true);
  });

  it('skips entries without property_api_name', () => {
    const raw = JSON.stringify([
      { property_api_name: 'customerId', column_name: 'customer_id', confidence: 'high' },
      { column_name: 'orphan', confidence: 'high' }, // no property_api_name
    ]);
    const result = parseMappingSuggestions(raw, props);
    expect(result).toHaveLength(3);
    // The orphan entry is dropped; the backfill still produces 3 rows.
    expect(result.find((r) => r.property_api_name === 'customerId')?.column_name).toBe('customer_id');
  });

  it('handles empty property list', () => {
    const raw = JSON.stringify([{ property_api_name: 'x', column_name: 'y', confidence: 'high' }]);
    const result = parseMappingSuggestions(raw, []);
    expect(result).toEqual([]);
  });
});
