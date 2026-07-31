import { describe, it, expect } from 'vitest';
import {
  availableSources,
  draftFromRecord,
  draftToPayload,
  draftToUpdatePayload,
  emptyDraft,
  ensureParameterForMapping,
  isParameterAutoDerived,
  systemContextOptions,
  validateDraft,
  newModifyRule,
  newCreateRule,
  newUpsertRule,
  newDeleteRule,
} from '../actionDraft';
import type { ActionTypeRecord, PropertyDef } from '../../types';

function makeProp(overrides: Partial<PropertyDef> = {}): PropertyDef {
  return {
    id: 'p1',
    object_type_id: 'ot1',
    api_name: 'status',
    display_name: '状态',
    description: '',
    data_type: 'STRING',
    is_primary_key: false,
    is_title_property: false,
    nullable: true,
    indexed: false,
    backing_mapping: null,
    created_at: '',
    updated_at: '',
    ...overrides,
  };
}

describe('emptyDraft', () => {
  it('creates a draft locked to the affected object type', () => {
    const d = emptyDraft('order');
    expect(d.affected_object_type_api_name).toBe('order');
    expect(d.parameters).toEqual([]);
    expect(d.ontology_rules).toEqual([]);
    expect(d.risk_level).toBe('low');
  });
});

describe('ensureParameterForMapping (机制 A)', () => {
  it('creates a same-name parameter when none exists', () => {
    const next = ensureParameterForMapping([], 'delayMinutes', 'INTEGER');
    expect(next).toHaveLength(1);
    expect(next[0].api_name).toBe('delayMinutes');
    expect(next[0].data_type).toBe('INTEGER');
    expect(next[0].required).toBe(true);
  });

  it('is a no-op when a same-name parameter already exists', () => {
    const existing = [
      { api_name: 'delayMinutes', display_name: 'd', data_type: 'INTEGER', required: true },
    ];
    const next = ensureParameterForMapping(existing, 'delayMinutes', 'INTEGER');
    expect(next).toBe(existing);
  });

  it('ignores empty param name', () => {
    expect(ensureParameterForMapping([], '', 'STRING')).toEqual([]);
  });
});

describe('isParameterAutoDerived', () => {
  it('true when a rule maps a PARAMETER source to this param name', () => {
    const rules = [
      {
        type: 'ModifyObject' as const,
        target_parameter: 'flight',
        properties: { status: { source: 'PARAMETER' as const, value: 'newStatus' } },
      },
    ];
    expect(isParameterAutoDerived('newStatus', rules)).toBe(true);
    expect(isParameterAutoDerived('other', rules)).toBe(false);
  });
});

describe('availableSources (机制 B)', () => {
  it('includes SYSTEM_GENERATED only for primary key', () => {
    const pk = availableSources('STRING', true, false);
    const nonPk = availableSources('STRING', false, false);
    expect(pk).toContain('SYSTEM_GENERATED');
    expect(nonPk).not.toContain('SYSTEM_GENERATED');
  });

  it('includes SYSTEM_CONTEXT for STRING/TIMESTAMP/DATE', () => {
    expect(availableSources('STRING', false, false)).toContain('SYSTEM_CONTEXT');
    expect(availableSources('TIMESTAMP', false, false)).toContain('SYSTEM_CONTEXT');
    expect(availableSources('INTEGER', false, false)).not.toContain('SYSTEM_CONTEXT');
  });

  it('includes OBJECT_PROPERTY only when an object-ref param exists', () => {
    expect(availableSources('STRING', false, true)).toContain('OBJECT_PROPERTY');
    expect(availableSources('STRING', false, false)).not.toContain('OBJECT_PROPERTY');
  });

  it('always offers PARAMETER, STATIC_VALUE, EXPRESSION', () => {
    const s = availableSources('INTEGER', false, false);
    expect(s).toContain('PARAMETER');
    expect(s).toContain('STATIC_VALUE');
    expect(s).toContain('EXPRESSION');
  });
});

describe('systemContextOptions', () => {
  it('returns CURRENT_TIMESTAMP for TIMESTAMP', () => {
    expect(systemContextOptions('TIMESTAMP')).toEqual(['CURRENT_TIMESTAMP']);
  });
  it('returns both for STRING', () => {
    expect(systemContextOptions('STRING')).toEqual(['CURRENT_USER_ID', 'CURRENT_TIMESTAMP']);
  });
  it('empty for non-string/timestamp', () => {
    expect(systemContextOptions('INTEGER')).toEqual([]);
  });
});

describe('validateDraft', () => {
  const props = [
    makeProp({ api_name: 'id', is_primary_key: true, data_type: 'STRING' }),
    makeProp({ api_name: 'status', data_type: 'STRING' }),
  ];

  it('flags empty api_name and display_name', () => {
    const d = emptyDraft('order');
    const errs = validateDraft(d, props, [], true);
    const fields = errs.map((e) => e.field);
    expect(fields).toContain('api_name');
    expect(fields).toContain('display_name');
  });

  it('flags invalid api_name format', () => {
    const d = { ...emptyDraft('order'), api_name: 'Bad_Name', display_name: 'X' };
    const errs = validateDraft(d, props, [], true);
    expect(errs.some((e) => e.field === 'api_name')).toBe(true);
  });

  it('flags duplicate api_name on create', () => {
    const d = { ...emptyDraft('order'), api_name: 'delayFlight', display_name: 'X' };
    const errs = validateDraft(d, props, ['delayFlight'], true);
    expect(errs.some((e) => e.field === 'api_name' && e.message.includes('已存在'))).toBe(true);
  });

  it('allows duplicate api_name on edit', () => {
    const d = { ...emptyDraft('order'), api_name: 'delayFlight', display_name: 'X' };
    const errs = validateDraft(d, props, ['delayFlight'], false);
    expect(errs.some((e) => e.field === 'api_name')).toBe(false);
  });

  it('flags ModifyObject with no property mappings', () => {
    const d = {
      ...emptyDraft('order'),
      api_name: 'delayOrder',
      display_name: 'X',
      parameters: [
        {
          api_name: 'order',
          display_name: 'o',
          data_type: 'STRING',
          required: true,
          object_type_ref: 'order',
        },
      ],
      ontology_rules: [newModifyRule('order')],
    };
    const errs = validateDraft(d, props, [], true);
    expect(errs.some((e) => e.message.includes('至少需要一条属性映射'))).toBe(true);
  });

  it('flags primary key in ModifyObject properties', () => {
    const d = {
      ...emptyDraft('order'),
      api_name: 'delayOrder',
      display_name: 'X',
      parameters: [
        {
          api_name: 'order',
          display_name: 'o',
          data_type: 'STRING',
          required: true,
          object_type_ref: 'order',
        },
      ],
      ontology_rules: [
        {
          ...newModifyRule('order'),
          properties: { id: { source: 'STATIC_VALUE' as const, value: 'x' } },
        },
      ],
    };
    const errs = validateDraft(d, props, [], true);
    expect(errs.some((e) => e.message.includes('主键') && e.message.includes('不可修改'))).toBe(
      true,
    );
  });

  it('flags two ops on the same target_parameter', () => {
    const d = {
      ...emptyDraft('order'),
      api_name: 'delayOrder',
      display_name: 'X',
      parameters: [
        {
          api_name: 'order',
          display_name: 'o',
          data_type: 'STRING',
          required: true,
          object_type_ref: 'order',
        },
      ],
      ontology_rules: [
        {
          ...newModifyRule('order'),
          properties: { status: { source: 'STATIC_VALUE' as const, value: 'x' } },
        },
        newDeleteRule('order'),
      ],
    };
    const errs = validateDraft(d, props, [], true);
    expect(
      errs.some((e) => e.message.includes('同一对象') && e.message.includes('只能有一个操作')),
    ).toBe(true);
  });

  it('flags CreateObject missing primary key mapping', () => {
    const d = {
      ...emptyDraft('order'),
      api_name: 'createOrder',
      display_name: 'X',
      ontology_rules: [newCreateRule('order')],
    };
    const errs = validateDraft(d, props, [], true);
    expect(errs.some((e) => e.message.includes('主键'))).toBe(true);
  });

  it('passes a valid ModifyObject draft', () => {
    const d = {
      ...emptyDraft('order'),
      api_name: 'delayOrder',
      display_name: 'Delay',
      parameters: [
        {
          api_name: 'order',
          display_name: 'o',
          data_type: 'STRING',
          required: true,
          object_type_ref: 'order',
        },
      ],
      ontology_rules: [
        {
          ...newModifyRule('order'),
          properties: { status: { source: 'STATIC_VALUE' as const, value: 'x' } },
        },
      ],
    };
    const errs = validateDraft(d, props, [], true);
    expect(errs).toEqual([]);
  });
});

describe('draftFromRecord / draftToPayload roundtrip', () => {
  it('unpacks parameters.ontology_rules / effects / parameters from the JSON blob', () => {
    const rec: ActionTypeRecord = {
      id: 'a1',
      ontology_id: 'o1',
      api_name: 'delayFlight',
      display_name: '航班延误',
      description: 'd',
      affected_object_type_id: 'ot1',
      parameters: {
        parameters: [
          {
            api_name: 'flight',
            display_name: 'f',
            data_type: 'STRING',
            required: true,
            object_type_ref: 'flight',
          },
        ],
        ontology_rules: [
          {
            type: 'ModifyObject',
            target_parameter: 'flight',
            properties: { status: { source: 'STATIC_VALUE' as const, value: 'Delayed' } },
          },
        ],
        effects: [
          {
            type: 'write_back',
            config: { target_object_type: 'flight', op: 'upsert' },
            trigger: 'AFTER_ONTOLOGY_CHANGE',
          },
        ],
      },
      rules: {
        rules: [{ type: 'validation', target: 'delay_minutes', expression: 'delay_minutes > 0' }],
      },
      submission_criteria: {},
      status: 'ACTIVE',
      risk_level: 'medium',
      operation_kind: 'update',
      batch_enabled: false,
      created_at: '',
      updated_at: '',
    };
    const d = draftFromRecord(rec);
    expect(d.api_name).toBe('delayFlight');
    expect(d.parameters).toHaveLength(1);
    expect(d.ontology_rules).toHaveLength(1);
    expect(d.ontology_rules[0].type).toBe('ModifyObject');
    expect(d.effects).toHaveLength(1);
    expect(d.rules).toHaveLength(1);
    expect(d.risk_level).toBe('medium');

    const payload = draftToPayload(d);
    expect(payload.affected_object_type_api_name).toBe(''); // filled by caller
    expect(payload.parameters).toEqual(d.parameters);
    expect(payload.ontology_rules).toEqual(d.ontology_rules);
  });

  it('update payload wraps parameters/rules into storage-format dicts', () => {
    const d = {
      ...emptyDraft('order'),
      api_name: 'a',
      display_name: 'b',
      parameters: [{ api_name: 'p', display_name: 'p', data_type: 'STRING', required: true }],
    };
    const u = draftToUpdatePayload(d);
    expect('affected_object_type_api_name' in u).toBe(false);
    expect(u.api_name).toBeUndefined(); // api_name 不可改，不传
    expect(u.parameters).toEqual({
      parameters: d.parameters,
      rules: [],
      effects: [],
      ontology_rules: [],
    });
    expect(u.rules).toEqual({ rules: [] });
  });
});

describe('default rule constructors', () => {
  it('newModifyRule defaults to raise_not_found', () => {
    expect(newModifyRule('x').on_missing).toBe('raise_not_found');
  });
  it('newUpsertRule defaults to create', () => {
    expect(newUpsertRule('x').on_missing).toBe('create');
  });
  it('newCreateRule seeds target_object_type when given', () => {
    expect(newCreateRule('flight').target_object_type).toBe('flight');
  });
});
