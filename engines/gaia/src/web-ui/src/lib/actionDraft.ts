/**
 * ActionType 编辑器草稿逻辑（ADR Action Mutation Mapping）。
 *
 * 纯函数模块，集中三件事：
 *  1. 草稿 ↔ 后端 payload 转换（ActionTypeCreatePayload）
 *  2. 机制 A：属性映射行自动派生同名参数（Palantir 对齐）
 *  3. 保存前前端校验（主键保护、规则冲突、必填）
 *
 * 抽出为独立模块以便单元测试，与 UI 解耦。
 */

import type {
  ActionEffectConfig,
  ActionParameterDef,
  ActionRule,
  ActionTypeCreatePayload,
  ActionTypeRecord,
  OntologyRule,
  PropertyDef,
  ValueSource,
} from '../types';

// ── 草稿类型 ──────────────────────────────────────────────────────────

/** 编辑器内部草稿（单一 source of truth）。 */
export interface ActionDraft {
  api_name: string;
  display_name: string;
  description: string;
  /** 目标对象 api_name（affected_object_type_api_name），新建时锁定。 */
  affected_object_type_api_name: string;
  parameters: ActionParameterDef[];
  rules: ActionRule[];
  ontology_rules: OntologyRule[];
  effects: ActionEffectConfig[];
  risk_level: 'low' | 'medium' | 'high';
  operation_kind: 'create' | 'update' | 'delete' | 'mixed';
  batch_enabled: boolean;
}

export function emptyDraft(affectedObjectType: string): ActionDraft {
  return {
    api_name: '',
    display_name: '',
    description: '',
    affected_object_type_api_name: affectedObjectType,
    parameters: [],
    rules: [],
    ontology_rules: [],
    effects: [],
    risk_level: 'low',
    operation_kind: 'mixed',
    batch_enabled: false,
  };
}

// ── 后端 ActionTypeRecord → 草稿（编辑回填） ─────────────────────────

/**
 * 从后端 ActionTypeRecord 回填草稿。
 *
 * 注意：后端把 parameters/ontology_rules/effects 都塞在 `parameters` JSON
 * 字段里（key 分别为 parameters / ontology_rules / effects），rules 在顶层
 * `rules.rules`。本函数负责解包。
 */
export function draftFromRecord(a: ActionTypeRecord): ActionDraft {
  const params = (a.parameters ?? {}) as Record<string, unknown>;
  const paramDefs = (params.parameters as ActionParameterDef[] | undefined) ?? [];
  const ontologyRules = (params.ontology_rules as OntologyRule[] | undefined) ?? [];
  const effects = (params.effects as ActionEffectConfig[] | undefined) ?? [];
  const rules =
    ((a.rules as Record<string, unknown> | undefined)?.rules as ActionRule[] | undefined) ?? [];

  return {
    api_name: a.api_name,
    display_name: a.display_name,
    description: a.description ?? '',
    affected_object_type_api_name: '', // 由调用方根据 affected_object_type_id 解析后填入
    parameters: paramDefs.map(normalizeParam),
    rules,
    ontology_rules: ontologyRules.map(normalizeRule),
    effects,
    risk_level: a.risk_level ?? 'low',
    operation_kind: a.operation_kind ?? 'mixed',
    batch_enabled: a.batch_enabled ?? false,
  };
}

/** 兼容旧字段名 default_value → default（字段对齐）。 */
function normalizeParam(p: ActionParameterDef): ActionParameterDef {
  const { default_value, ...rest } = p as ActionParameterDef & { default_value?: unknown };
  if (rest.default === undefined && default_value !== undefined) {
    return { ...rest, default: default_value };
  }
  return rest;
}

function normalizeRule(r: OntologyRule): OntologyRule {
  return {
    ...r,
    properties: r.properties ?? {},
    on_missing: r.on_missing ?? 'raise_not_found',
  };
}

// ── 草稿 → 后端 payload（保存） ───────────────────────────────────────

/** 清理 OntologyRule.properties：移除空 propName 的临时行（保存前）。 */
function cleanRuleProperties(rule: OntologyRule): OntologyRule {
  const props = rule.properties ?? {};
  const cleaned: Record<string, ValueSource> = {};
  for (const [k, v] of Object.entries(props)) {
    if (k && k !== '__empty__') cleaned[k] = v;
  }
  return { ...rule, properties: cleaned };
}

export function draftToPayload(d: ActionDraft): ActionTypeCreatePayload {
  return {
    api_name: d.api_name,
    display_name: d.display_name,
    description: d.description,
    affected_object_type_api_name: d.affected_object_type_api_name,
    parameters: d.parameters,
    rules: d.rules,
    ontology_rules: d.ontology_rules.map(cleanRuleProperties),
    effects: d.effects,
    risk_level: d.risk_level,
    operation_kind: d.operation_kind,
    batch_enabled: d.batch_enabled,
  };
}

/** 编辑态保存：构造 PATCH 的 updates dict（后端 update_action_type 接收 dict）。
 *
 * 后端把 parameters/rules/effects/ontology_rules 都压进 ORM 的 `parameters`
 * JSON 列（存储格式 = {parameters:[...], rules:[...], effects:[...],
 * ontology_rules:[...]}），`rules` 列存 {rules:[...]}。故 update 时必须传
 * 存储格式的 dict，而非 ActionTypeCreate 的 list 格式（与 define_action_type
 * 的包装逻辑对齐）。
 */
export function draftToUpdatePayload(d: ActionDraft): Record<string, unknown> {
  return {
    display_name: d.display_name,
    description: d.description,
    risk_level: d.risk_level,
    operation_kind: d.operation_kind,
    batch_enabled: d.batch_enabled,
    parameters: {
      parameters: d.parameters,
      rules: d.rules,
      effects: d.effects,
      ontology_rules: d.ontology_rules.map(cleanRuleProperties),
    },
    rules: { rules: d.rules },
    submission_criteria: [],
  };
}

// ── 机制 A：属性映射自动派生参数 ──────────────────────────────────────

/**
 * 当用户在规则属性映射行选择了「参数」值来源但目标参数尚不存在时，
 * 自动创建一个同名参数（Palantir 行为：每添加一个属性默认绑定同名参数）。
 *
 * @returns 新的 parameters 数组（若已存在同名参数则原样返回）。
 */
export function ensureParameterForMapping(
  parameters: ActionParameterDef[],
  paramName: string,
  dataType: string,
  opts: { object_type_ref?: string | null } = {},
): ActionParameterDef[] {
  if (!paramName) return parameters;
  if (parameters.some((p) => p.api_name === paramName)) return parameters;
  const newParam: ActionParameterDef = {
    api_name: paramName,
    display_name: paramName,
    data_type: dataType,
    required: true,
    object_type_ref: opts.object_type_ref ?? null,
  };
  return [...parameters, newParam];
}

/** 标记参数是否由规则自动派生（用于 UI 显示 ⓘ 图标）。 */
export function isParameterAutoDerived(paramName: string, ontologyRules: OntologyRule[]): boolean {
  return ontologyRules.some((rule) =>
    Object.values(rule.properties ?? {}).some(
      (vs) => vs.source === 'PARAMETER' && vs.value === paramName,
    ),
  );
}

// ── 值来源可用性（机制 B：按属性类型收窄） ────────────────────────────

export type ValueSourceKind = ValueSource['source'];

/**
 * 根据目标属性的数据类型 + 是否为主键 + 是否存在对象引用参数，
 * 返回该属性映射行可选的值来源列表（Palantir 对齐的动态收窄）。
 */
export function availableSources(
  propType: string,
  isPrimaryKey: boolean,
  hasObjectRefParam: boolean,
): ValueSourceKind[] {
  const sources: ValueSourceKind[] = ['PARAMETER', 'STATIC_VALUE'];
  if (isPrimaryKey) {
    sources.push('SYSTEM_GENERATED');
  }
  const t = propType.toUpperCase();
  if (t === 'STRING' || t === 'TIMESTAMP' || t === 'DATE') {
    sources.push('SYSTEM_CONTEXT');
  }
  if (hasObjectRefParam) {
    sources.push('OBJECT_PROPERTY');
  }
  sources.push('EXPRESSION');
  return sources;
}

/** SYSTEM_CONTEXT 可选值（按属性类型过滤）。 */
export function systemContextOptions(propType: string): string[] {
  const t = propType.toUpperCase();
  if (t === 'TIMESTAMP' || t === 'DATE') return ['CURRENT_TIMESTAMP'];
  if (t === 'STRING') return ['CURRENT_USER_ID', 'CURRENT_TIMESTAMP'];
  return [];
}

// ── 校验（保存前拦截，ADR §3.8） ──────────────────────────────────────

export interface ValidationError {
  field: string;
  message: string;
}

const API_NAME_PATTERN = /^[a-z][a-zA-Z0-9]*$/;

/** 校验草稿，返回错误列表（空数组=可保存）。 */
export function validateDraft(
  d: ActionDraft,
  targetObjectTypeProps: PropertyDef[],
  existingApiNames: string[] = [],
  isCreate: boolean,
): ValidationError[] {
  const errors: ValidationError[] = [];

  // 1. api_name 格式 + 唯一
  if (!d.api_name) {
    errors.push({ field: 'api_name', message: 'API 名称不能为空' });
  } else if (!API_NAME_PATTERN.test(d.api_name)) {
    errors.push({
      field: 'api_name',
      message: 'API 名称须以小写字母开头，仅含字母数字（camelCase）',
    });
  } else if (isCreate && existingApiNames.includes(d.api_name)) {
    errors.push({ field: 'api_name', message: `API 名称 "${d.api_name}" 已存在` });
  }

  // 2. 显示名称
  if (!d.display_name.trim()) {
    errors.push({ field: 'display_name', message: '显示名称不能为空' });
  }

  // 3. 目标对象
  if (!d.affected_object_type_api_name) {
    errors.push({ field: 'affected_object_type_api_name', message: '必须指定目标对象类型' });
  }

  const pkApiName = targetObjectTypeProps.find((p) => p.is_primary_key)?.api_name;
  const isEmptyProp = (k: string) => !k || k === '__empty__';

  // 4. 逐条规则校验
  const seenTargets = new Map<string, string>(); // target_parameter → rule type
  d.ontology_rules.forEach((rule, i) => {
    const ctx = `ontology_rules[${i}]`;

    if (rule.type === 'CreateObject') {
      if (!rule.target_object_type) {
        errors.push({ field: ctx, message: '创建对象规则必须指定目标对象类型' });
      }
      // CreateObject 必须映射主键（若目标对象类型与当前对象一致）
      if (
        rule.target_object_type === d.affected_object_type_api_name &&
        pkApiName &&
        !Object.keys(rule.properties ?? {})
          .filter((k) => !isEmptyProp(k))
          .includes(pkApiName)
      ) {
        errors.push({
          field: ctx,
          message: `创建对象必须映射主键属性 "${pkApiName}"`,
        });
      }
    } else if (rule.type === 'DeleteObject') {
      if (!rule.target_parameter) {
        errors.push({ field: ctx, message: '删除对象规则必须指定目标参数' });
      }
    } else {
      // ModifyObject / UpsertObject
      if (!rule.target_parameter) {
        errors.push({ field: ctx, message: `${rule.type} 必须指定目标参数` });
      }
      if (Object.keys(rule.properties ?? {}).filter((k) => !isEmptyProp(k)).length === 0) {
        errors.push({ field: ctx, message: `${rule.type} 至少需要一条属性映射` });
      }
      // 主键不可出现在 Modify 的 properties
      if (rule.type === 'ModifyObject' && pkApiName) {
        if (Object.keys(rule.properties ?? {}).includes(pkApiName)) {
          errors.push({
            field: ctx,
            message: `主键 "${pkApiName}" 不可修改，请从属性映射中移除`,
          });
        }
      }
    }

    // 链接规则
    if (rule.type === 'CreateLink' || rule.type === 'DeleteLink') {
      if (!rule.link_type) {
        errors.push({ field: ctx, message: `${rule.type} 必须指定关联类型` });
      }
      if (!rule.source_parameter || !rule.target_link_parameter) {
        errors.push({ field: ctx, message: `${rule.type} 必须指定源参数和目标参数` });
      }
    }

    // 同一 target_parameter 只能有一个 op（Modify/Upsert/Delete 之间）
    if (
      rule.target_parameter &&
      ['ModifyObject', 'UpsertObject', 'DeleteObject'].includes(rule.type)
    ) {
      const prev = seenTargets.get(rule.target_parameter);
      if (prev) {
        errors.push({
          field: ctx,
          message: `同一对象 "${rule.target_parameter}" 已有规则 (${prev})，同一对象只能有一个操作`,
        });
      } else {
        seenTargets.set(rule.target_parameter, rule.type);
      }
    }
  });

  // 5. 校验规则表达式非空
  d.rules.forEach((r, i) => {
    if (!r.expression?.trim()) {
      errors.push({ field: `rules[${i}]`, message: '校验规则表达式不能为空' });
    }
  });

  return errors;
}

// ── 默认规则构造器（「+ 添加规则」用） ────────────────────────────────

export function newModifyRule(targetParameter?: string): OntologyRule {
  return {
    type: 'ModifyObject',
    target_parameter: targetParameter ?? null,
    properties: {},
    on_missing: 'raise_not_found',
    condition: null,
    description: '',
  };
}

export function newCreateRule(targetObjectType?: string): OntologyRule {
  return {
    type: 'CreateObject',
    target_object_type: targetObjectType ?? null,
    properties: {},
    condition: null,
    description: '',
  };
}

export function newUpsertRule(targetParameter?: string): OntologyRule {
  return {
    type: 'UpsertObject',
    target_parameter: targetParameter ?? null,
    properties: {},
    on_missing: 'create',
    condition: null,
    description: '',
  };
}

export function newDeleteRule(targetParameter?: string): OntologyRule {
  return {
    type: 'DeleteObject',
    target_parameter: targetParameter ?? null,
    properties: {},
    condition: null,
    description: '',
  };
}

export function newLinkRule(type: 'CreateLink' | 'DeleteLink'): OntologyRule {
  return {
    type,
    link_type: null,
    source_parameter: null,
    target_link_parameter: null,
    properties: {},
    condition: null,
    description: '',
  };
}

/** 规则类型的中文标签。 */
export const RULE_TYPE_LABELS: Record<OntologyRule['type'], string> = {
  CreateObject: '创建对象',
  ModifyObject: '修改对象',
  UpsertObject: '创建或修改对象',
  DeleteObject: '删除对象',
  CreateLink: '创建关联',
  DeleteLink: '删除关联',
};

/** 副作用类型标签（统一「+ 添加规则」下拉）。 */
export const EFFECT_TYPE_LABELS: Record<ActionEffectConfig['type'], string> = {
  notification: '通知',
  webhook: 'Webhook',
  write_back: '回写源表',
  sub_action: '触发子动作',
  kafka_topic: '发布 Kafka 事件',
};
