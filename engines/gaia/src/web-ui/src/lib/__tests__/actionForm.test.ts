import { describe, it, expect } from 'vitest';
import { coerceValue, extractParamDefs, stringifyParams, controlKindFor } from '../actionForm';
import type { ActionTypeRecord, ActionParameterDef } from '../../types';

describe('coerceValue', () => {
  it('returns null for empty string', () => {
    expect(coerceValue('', 'STRING')).toBeNull();
    expect(coerceValue(undefined, 'STRING')).toBeNull();
  });

  it('coerces INTEGER', () => {
    expect(coerceValue('42', 'INTEGER')).toBe(42);
    expect(coerceValue('-7', 'LONG')).toBe(-7);
  });

  it('coerces FLOAT/DOUBLE', () => {
    expect(coerceValue('3.14', 'FLOAT')).toBeCloseTo(3.14);
    expect(coerceValue('10', 'DOUBLE')).toBe(10);
  });

  it('coerces BOOLEAN (case-insensitive)', () => {
    expect(coerceValue('true', 'BOOLEAN')).toBe(true);
    expect(coerceValue('TRUE', 'BOOLEAN')).toBe(true);
    expect(coerceValue('false', 'BOOLEAN')).toBe(false);
    expect(coerceValue('anything', 'BOOLEAN')).toBe(false);
  });

  it('returns string for STRING and unknown types', () => {
    expect(coerceValue('hello', 'STRING')).toBe('hello');
    expect(coerceValue('x', 'UNKNOWN')).toBe('x');
  });
});

describe('extractParamDefs', () => {
  it('extracts parameters from ActionTypeRecord', () => {
    const action = {
      parameters: { parameters: [{ api_name: 'status', data_type: 'STRING' }] },
    } as unknown as ActionTypeRecord;
    const defs = extractParamDefs(action);
    expect(defs).toHaveLength(1);
    expect(defs[0].api_name).toBe('status');
  });

  it('returns empty array when no parameters key', () => {
    const action = { parameters: {} } as unknown as ActionTypeRecord;
    expect(extractParamDefs(action)).toEqual([]);
  });
});

describe('stringifyParams', () => {
  it('stringifies values', () => {
    expect(stringifyParams({ a: 1, b: 'x', c: null })).toEqual({
      a: '1',
      b: 'x',
      c: '',
    });
  });
});

describe('controlKindFor', () => {
  it('returns checkbox for BOOLEAN', () => {
    const def: ActionParameterDef = { api_name: 'flag', data_type: 'BOOLEAN' };
    expect(controlKindFor(def)).toBe('checkbox');
  });

  it('returns date for DATE', () => {
    const def: ActionParameterDef = { api_name: 'd', data_type: 'DATE' };
    expect(controlKindFor(def)).toBe('date');
  });

  it('returns datetime-local for TIMESTAMP', () => {
    const def: ActionParameterDef = { api_name: 't', data_type: 'TIMESTAMP' };
    expect(controlKindFor(def)).toBe('datetime-local');
  });

  it('returns select when enum_values present', () => {
    const def: ActionParameterDef = {
      api_name: 'p',
      data_type: 'STRING',
      enum_values: ['low', 'high'],
    };
    expect(controlKindFor(def)).toBe('select');
  });

  it('returns object-ref when object_type_ref present', () => {
    const def: ActionParameterDef = {
      api_name: 'cust',
      data_type: 'STRING',
      object_type_ref: 'customer',
    };
    expect(controlKindFor(def)).toBe('object-ref');
  });

  it('returns text by default', () => {
    const def: ActionParameterDef = { api_name: 'name', data_type: 'STRING' };
    expect(controlKindFor(def)).toBe('text');
  });
});
