// TypeScript types matching Gaia backend schemas

export interface Ontology {
  id: string;
  api_name: string;
  display_name: string;
  description: string;
  rid: string;
  object_types_count: number;
  /** v5.2 lifecycle: ACTIVE | DEPRECATED */
  status: 'ACTIVE' | 'DEPRECATED';
  /** v5.2: set when soft-deleted; null when active. */
  deleted_at: string | null;
  created_at: string;
  updated_at: string;
}

export interface OntologyCreate {
  api_name: string;
  display_name: string;
  description?: string;
}

export interface OntologyUpdate {
  display_name?: string;
  description?: string;
  /** v5.2: set to 'DEPRECATED' to Deprecate (precondition for delete). */
  status?: 'ACTIVE' | 'DEPRECATED';
}

// ── v5.2 delete-governance: cascade impact report (design §六) ──

export interface ImpactItem {
  resource_type: string;
  count: number;
  label: string;
}

export interface ImpactReport {
  api_name: string;
  status: string;
  impacts: ImpactItem[];
  can_delete: boolean;
  blocked_reason: string | null;
}

export interface BackingColumnRef {
  /** 对标 Palantir mapping.column.backingColumn — 底层 Dataset 物理列引用。 */
  dataset_api_name?: string;
  backing_catalog: string;
  backing_schema: string;
  backing_table: string;
  backing_column: string;
}

export type StorageType = 'MANAGED' | 'VIRTUAL';

/** ObjectType-level opt-in switches for enhanced indexing (ADR-015 §capabilities).
 * Mirrors Palantir Foundry's Ontology Manager Capabilities tab. */
export interface ObjectTypeCapabilities {
  graph_indexing_enabled: boolean;
  geotime_indexing_enabled: boolean;
}
export type Visibility = 'NORMAL' | 'PROMINENT' | 'HIDDEN';
export type ObjectStatus = 'ACTIVE' | 'ENDORSED' | 'EXPERIMENTAL' | 'DEPRECATED';
export type Cardinality = 'ONE' | 'MANY';
export type Direction = 'OUTGOING' | 'INCOMING';

export type DataType =
  | 'STRING'
  | 'INTEGER'
  | 'SHORT'
  | 'LONG'
  | 'BOOLEAN'
  | 'BYTE'
  | 'FLOAT'
  | 'DOUBLE'
  | 'DECIMAL'
  | 'DATE'
  | 'TIMESTAMP'
  | 'ARRAY'
  | 'STRUCT'
  | 'VECTOR'
  | 'GEOPOINT'
  | 'GEOSHAPE'
  | 'GEOTEMPORAL_SERIES'
  | 'TIME_SERIES'
  | 'MEDIA_REFERENCE'
  | 'ATTACHMENT';

export interface PropertyDef {
  id: string;
  object_type_id: string;
  api_name: string;
  display_name: string;
  description: string;
  data_type: DataType;
  is_primary_key: boolean;
  is_title_property: boolean;
  nullable: boolean;
  indexed: boolean;
  backing_mapping: BackingColumnRef | null;
  created_at: string;
  updated_at: string;
}

export interface PropertyDefCreate {
  /** apiName 由后端从 displayName/backing_column 推导，前端不填。 */
  display_name: string;
  description?: string;
  data_type: DataType;
  is_primary_key?: boolean;
  is_title_property?: boolean;
  nullable?: boolean;
  indexed?: boolean;
  backing_mapping?: BackingColumnRef | null;
}

export interface LinkTypeDef {
  id: string;
  ontology_id: string;
  api_name: string;
  display_name: string;
  description: string;
  source_object_type_id: string;
  target_object_type_id: string;
  foreign_key_property_api_name: string | null;
  cardinality: Cardinality;
  direction: Direction;
  created_at: string;
  updated_at: string;
}

export interface LinkTypeDefCreate {
  /** apiName 由后端从 display_name 推导 (camelCase)，前端不填。 */
  display_name: string;
  description?: string;
  source_object_type_id: string;
  target_object_type_id: string;
  foreign_key_property_api_name?: string;
  cardinality: Cardinality;
  direction: Direction;
}

export interface ObjectType {
  id: string;
  ontology_id: string;
  api_name: string;
  display_name: string;
  description: string;
  primary_key: string;
  title_property: string;
  storage_type: StorageType;
  visibility: Visibility;
  status: ObjectStatus;
  /** Primary backing dataset (convenience ref; authoritative binding is per-property backing_mapping). None when unbound. */
  backing_dataset_api_name: string | null;
  properties: PropertyDef[];
  links: LinkTypeDef[];
  capabilities: ObjectTypeCapabilities;
  created_at: string;
  updated_at: string;
}

/** Lightweight ObjectType for list/table/sidebar — no property details. */
export interface ObjectTypeSummary {
  id: string;
  ontology_id: string;
  api_name: string;
  display_name: string;
  description: string;
  storage_type: StorageType;
  visibility: Visibility;
  status: ObjectStatus;
  /** ADR-016 option A: the Project this definition belongs to. */
  project_id: string | null;
  /** Primary backing dataset (convenience ref for list badges). None when unbound. */
  backing_dataset_api_name: string | null;
  properties_count: number;
  links_count: number;
  actions_count: number;
  created_at: string;
  updated_at: string;
}

export interface ObjectTypeCreate {
  /** apiName: PascalCase, caller-supplied (frontend LLM-derives from display_name, user confirms/edits). */
  api_name: string;
  display_name: string;
  description?: string;
  /** 可选：省略时由后端从属性的 is_primary_key 标记反推 (Q2)。 */
  primary_key?: string;
  /** 可选：省略时由后端从属性的 is_title_property 标记反推 (Q2)。 */
  title_property?: string;
  storage_type: StorageType;
  visibility?: Visibility;
  status?: ObjectStatus;
  /** Optional primary backing dataset; typically populated by the first link_dataset call, not at creation. */
  backing_dataset_api_name?: string | null;
  capabilities?: ObjectTypeCapabilities;
}

export interface ActionTypeRecord {
  id: string;
  ontology_id: string;
  api_name: string;
  display_name: string;
  description: string;
  affected_object_type_id: string | null;
  parameters: Record<string, unknown>;
  rules: Record<string, unknown>;
  submission_criteria: Record<string, unknown>;
  status: string;
  /** P1 (ADR-011) */
  risk_level?: 'low' | 'medium' | 'high';
  version?: number;
  operation_kind?: 'create' | 'update' | 'delete' | 'mixed';
  batch_enabled?: boolean;
  /** ADR Action Mutation Mapping: 声明式 Ontology Rules。 */
  ontology_rules?: OntologyRule[];
  created_at: string;
  updated_at: string;
}

/** Payload for creating an ActionType (对齐后端 ActionTypeCreate)。 */
export interface ActionTypeCreatePayload {
  api_name: string;
  display_name: string;
  description?: string;
  affected_object_type_api_name: string;
  parameters: ActionParameterDef[];
  rules?: ActionRule[];
  submission_criteria?: SubmissionCriterion[] | Record<string, string>;
  effects?: ActionEffectConfig[];
  ontology_rules?: OntologyRule[];
  risk_level?: 'low' | 'medium' | 'high';
  operation_kind?: 'create' | 'update' | 'delete' | 'mixed';
  batch_enabled?: boolean;
}

/** ADR Action Mutation Mapping: 属性值来源。 */
export interface ValueSource {
  source:
    | 'PARAMETER'
    | 'OBJECT_PROPERTY'
    | 'STATIC_VALUE'
    | 'SYSTEM_CONTEXT'
    | 'SYSTEM_GENERATED'
    | 'EXPRESSION';
  value?: string | null;
}

/** ADR Action Mutation Mapping: 声明式 Ontology Rule。 */
export interface OntologyRule {
  type:
    'CreateObject' | 'ModifyObject' | 'UpsertObject' | 'DeleteObject' | 'CreateLink' | 'DeleteLink';
  target_parameter?: string | null;
  target_object_type?: string | null;
  target_path?: string | null;
  properties: Record<string, ValueSource>;
  link_type?: string | null;
  source_parameter?: string | null;
  target_link_parameter?: string | null;
  condition?: string | null;
  on_missing?: 'raise_not_found' | 'create';
  description?: string;
}

/** ADR Action Mutation Mapping: write_back effect 配置。 */
export interface WriteBackEffectConfig {
  target_object_type: string;
  op: 'upsert' | 'insert';
}

/** Side effect configuration for an Action (ADR Action Mutation Mapping). */
export interface ActionEffectConfig {
  type: 'webhook' | 'write_back' | 'sub_action' | 'kafka_topic' | 'notification';
  config: Record<string, unknown>;
  trigger: 'BEFORE_ONTOLOGY_CHANGE' | 'AFTER_ONTOLOGY_CHANGE';
  condition?: string | null;
}

/** Declarative validation/derivation/constraint rule (P1, ADR-011). */
export interface ActionRule {
  type: 'constraint' | 'derivation' | 'validation';
  target: string;
  expression: string;
  description?: string;
}

/** A single global submission criterion (P1, ADR-011). */
export interface SubmissionCriterion {
  expression: string;
  error_message: string;
  description?: string;
}

/** Action parameter definition (Palantir ActionType.parameters equivalent). */
export interface ActionParameterDef {
  api_name: string;
  display_name?: string;
  data_type: string;
  required?: boolean;
  /** 后端字段名为 `default`（与 ORM/schema 对齐）；旧名 `default_value` 已废弃。 */
  default?: unknown;
  /** P1 (ADR-011) */
  default_source?:
    'static' | 'current_user' | 'current_timestamp' | 'workspace_id' | 'selected_object_field';
  default_source_field?: string | null;
  readonly?: boolean;
  hidden?: boolean;
  pattern?: string | null;
  error_message?: string | null;
  enum_values?: string[] | null;
  object_type_ref?: string | null;
  is_object_set?: boolean;
}

/** Request payload for executing an action. */
export interface ActionExecutionRequest {
  parameters: Record<string, unknown>;
  idempotency_key?: string | null;
}

/** Result of an action execution. */
export interface ActionExecutionResult {
  status: 'applied' | 'accepted' | 'conflict' | 'validation_failed';
  action_id: string;
  affected_objects: Record<string, number>;
  mutations: Record<string, unknown>[];
  validation_errors: string[];
  conflict_details?: Record<string, unknown> | null;
  /** P1 (ADR-011): objects the caller lacked row-level write permission for. */
  forbidden_objects?: string[];
}

/** P1 (ADR-011): dry-run preview result. */
export interface ActionPreviewResult {
  valid: boolean;
  validation_errors: string[];
  mutations: Record<string, unknown>[];
  before_snapshots: Record<string, unknown>;
  derived_parameters: Record<string, unknown>;
}

/** P1 (ADR-011): historical ActionType version. */
export interface ActionTypeVersion {
  id: string;
  action_type_id: string;
  version: number;
  snapshot: Record<string, unknown>;
  published_by: string;
  created_at: string;
}

// ── P2: Batch Action (ADR-011 follow-up) ──

/** A single target within a Batch Action. */
export interface BatchActionItem {
  rid: string;
  parameters?: Record<string, unknown>;
  idempotency_key?: string | null;
  expected_version?: number;
}

/** Request payload for a Batch Action. */
export interface BatchActionRequest {
  items: BatchActionItem[];
  default_parameters?: Record<string, unknown>;
  idempotency_key?: string | null;
  shard_size?: number | null;
  fail_fast?: boolean;
}

/** Per-item outcome within a BatchActionResult. */
export interface BatchItemResult {
  rid: string;
  status:
    | 'applied'
    | 'accepted'
    | 'conflict'
    | 'validation_failed'
    | 'not_found'
    | 'forbidden'
    | 'error';
  action_id?: string | null;
  new_version?: number | null;
  error?: string | null;
}

/** Aggregate result of a Batch Action. */
export interface BatchActionResult {
  status: 'applied' | 'partial' | 'failed' | 'rejected';
  total: number;
  applied: number;
  failed: number;
  accepted: number;
  item_results: BatchItemResult[];
  shards_committed: number;
  shards_total: number;
  first_error?: string | null;
}

// ── Data Source (Data Layer v1.0) ──

export interface DataSource {
  id: string;
  api_name: string;
  display_name: string;
  description: string;
  connector_type: string;
  connector_config: Record<string, string>;
  credential_id: string | null;
  status: string;
  gravitino_catalog_name: string;
  capabilities: string[];
  created_at: string;
  updated_at: string;
}

export interface DataSourceCreate {
  api_name: string;
  display_name: string;
  description?: string;
  connector_type: string;
  connector_config: Record<string, string>;
  credential_id?: string | null;
}

export interface DataSourceUpdate {
  display_name?: string;
  description?: string;
  connector_config?: Record<string, string>;
  credential_id?: string | null;
}

export interface Credential {
  id: string;
  api_name: string;
  credential_type: string;
  secret_data: string;
  created_at: string;
}

export interface CredentialCreate {
  api_name: string;
  credential_type: string;
  secret_data: Record<string, unknown>;
}

export interface SyncTask {
  id: string;
  api_name: string;
  data_source_id: string;
  sync_type: string;
  source_config: Record<string, unknown>;
  target_dataset_api_name: string;
  sync_mode: string;
  transaction_type: string;
  allow_schema_changes: boolean;
  max_duration_minutes: number | null;
  file_filters: Record<string, unknown> | null;
  schedule: Record<string, unknown> | null;
  status: string;
  pipeline_name: string | null;
  last_run_at: string | null;
  created_at: string;
  updated_at: string;
}

export interface SyncTaskCreate {
  api_name: string;
  source_config: Record<string, unknown>;
  target_dataset_api_name: string;
  sync_type?: string;
  sync_mode?: string;
  transaction_type?: string;
  allow_schema_changes?: boolean;
  max_duration_minutes?: number | null;
  file_filters?: Record<string, unknown> | null;
  schedule?: Record<string, unknown> | null;
}

export interface DatasetGovernance {
  id: string;
  api_name: string;
  display_name: string;
  storage_location: string;
  partition_config: Record<string, unknown> | null;
  source_dataset_api_name: string | null;
  data_source_api_name: string | null;
  kind: 'MANAGED' | 'VIRTUAL';
  is_view: boolean;
  row_count_estimate: number | null;
  created_at: string;
  updated_at: string;
}

/** Dataset → ontology reverse-lookup entry (from /api/datasets/ontology-map). */
export interface DatasetOntologyRef {
  ontology_id: string;
  ontology_api_name: string;
  /** 本体中文名（display_name），无则为空串。 */
  ontology_display_name: string;
  object_type_api_name: string;
}

/** 分页数据集列表响应（GET /api/datasets/paginated）。 */
export interface PaginatedDatasets {
  items: DatasetGovernance[];
  total: number;
  page: number;
  page_size: number;
}

export interface TableInfo {
  name: string;
  schema: string;
  row_count_estimate: number | null;
  columns: ColumnInfo[];
  comment?: string;
}

export interface ColumnInfo {
  name: string;
  data_type: string;
  nullable: boolean;
  is_primary_key: boolean;
  comment: string;
}

export interface ExploreResult {
  database: string;
  tables: TableInfo[];
}

export interface ImpactAnalysis {
  severity: string;
  action: string;
  target_api_name: string;
  target_type: string;
  impacts: ImpactItem[];
}

export interface ImpactItem {
  resource_type: string;
  api_name: string;
  effect: string;
}

export interface ConnectionTestResult {
  success: boolean;
  message: string;
  details: Record<string, unknown> | null;
}

// Legacy aliases for backward compatibility
export type DataSourceDef = DataSource;
export type DataSourceCreateLegacy = {
  name: string;
  source_type: string;
  config?: Record<string, unknown>;
};

// ── AI Suggestions ──

export interface AiPropertySuggestion {
  api_name: string;
  display_name: string;
  description: string;
  data_type: DataType;
  is_primary_key: boolean;
  is_title_property: boolean;
  indexed: boolean;
}

export interface AiLinkSuggestion {
  api_name: string;
  display_name: string;
  target_object_type: string;
  cardinality: Cardinality;
}

export interface AiObjectTypeSuggestion {
  api_name: string;
  display_name: string;
  description: string;
  storage_type: StorageType;
  properties: AiPropertySuggestion[];
  links: AiLinkSuggestion[];
}

export interface AiGenerateResponse {
  suggestions: AiObjectTypeSuggestion[];
}

// ── Object loading (POST /objects/textsql) ──────────────────────────

/** 加载的对象实例（属性 → 值的字典，含主键）。 */
export type LoadedObject = Record<string, unknown>;

// ── Graph reasoning (graph-reasoning-frontend-design.md) ─────────────

/** ObjectSet IR —— 推理线传输层契约（对齐 Palantir ObjectSet）。 */
export interface ObjectSetIR {
  type: 'objectType' | 'static' | 'filter' | 'searchAround' | 'union' | 'intersect' | 'subtract' | 'aggregate' | 'select' | 'withProperties' | 'reference' | 'interfaceBase' | 'interfaceLinkSearchAround';
  /** objectType: 目标 ObjectType api_name */
  object_type?: string;
  /** objectType / filter: 内联过滤 */
  filters?: GraphFilter[];
  /** static: rid 列表 */
  objects?: string[];
  /** filter / searchAround / aggregate / select: 子 ObjectSet（嵌套） */
  object_set?: ObjectSetIR;
  /** union/intersect/subtract: 子 ObjectSet 列表（≥2） */
  object_sets?: ObjectSetIR[];
  /** searchAround: 遍历的 link api_name */
  link?: string;
  /** searchAround: 跳数范围 [min, max] */
  hops?: [number, number];
  /** searchAround: 方向 */
  direction?: 'out' | 'in' | 'both';
  /** 可选：排序（保证 cursor 分页稳定） */
  order_by?: Array<{ field: string; desc?: boolean }>;
  /** aggregate: 分组字段列表（可空=全局聚合） */
  group_by?: string[];
  /** aggregate: 聚合函数列表 */
  aggregations?: Array<{ func: 'count' | 'sum' | 'avg' | 'min' | 'max'; field: string; alias?: string }>;
  /** filter/objectType 可选：嵌套逻辑组合（and/or/not），对齐 Palantir SearchJsonQueryV2 */
  where?: WhereClause;
  /** withProperties: 派生属性定义（实验性） */
  derived_properties?: Record<string, unknown>;
  /** reference: 引用的 ObjectSet RID */
  reference?: string;
  /** interfaceBase/interfaceLinkSearchAround: Interface api_name */
  interface?: string;
}

/** where 子句判别联合（嵌套逻辑组合）。 */
export type WhereClause =
  | GraphFilter
  | { type: 'and'; value: WhereClause[] }
  | { type: 'or'; value: WhereClause[] }
  | { type: 'not'; value: WhereClause };

/** 过滤条件（白名单校验 field 必须在本体 properties 内）。 */
export interface GraphFilter {
  field: string;
  op:
    | 'exactMatch'
    | 'notEqual'
    | 'in'
    | 'notIn'
    | 'range'
    | 'greaterThan'
    | 'lessThan'
    | 'contains'
    | 'startsWith'
    | 'endsWith'
    | 'withinDistance'
    | 'withinPolygon'
    | 'withinBoundingBox'
    | 'timeRange'
    | 'isNull'
    | 'isNotNull';
  value?: unknown;
  coords?: number[][];
  center?: number[];
  max_distance?: number;
}

/** 图节点（画布渲染用）。 */
export interface GraphNode {
  rid: string;
  api_name: string;
  props: Record<string, unknown>;
}

/** 推理查询结果。 */
export interface ReasoningResult {
  objects: GraphNode[];
  /** aggregate 结果（仅 type=aggregate 时非空） */
  aggregates: Array<{ group: Record<string, unknown>; aggregates: Record<string, unknown> }>;
  truncated: boolean;
  next_cursor: string | null;
  stats: {
    steps: number;
    engines_used: string[];
    timings: Record<string, number>;
    total_vids: number;
    hydrated: number;
    groups?: number;
  };
  evidence_id: string | null;
}

/** traverse_link 请求。 */
export interface TraverseRequest {
  link_type: string;
  source_keys: string[];
  direction?: 'forward' | 'reverse';
  target_filter?: Record<string, unknown>;
  target_properties?: string[];
  include_source_mapping?: boolean;
}

/** traverse_link 结果。 */
export interface TraverseResult {
  target_objects: GraphNode[];
  source_to_target_map?: Record<string, string[]>;
  error?: { code: string; message: string };
}

/** exists_link 请求。 */
export interface ExistsRequest {
  link_type: string;
  source_key: string;
  direction?: 'forward' | 'reverse';
  target_key?: string | null;
}

/** exists_link 结果。 */
export interface ExistsResult {
  exists: boolean;
  mode: 'ANY_TARGET' | 'SINGLE_TARGET';
  error?: { code: string; message: string };
}

/** 证据链快照（analysis_records）。 */
export interface AnalysisRecord {
  id: string;
  ontology_id: string;
  principal: string;
  object_set_ir: ObjectSetIR;
  result_summary: {
    steps: number;
    engines_used: string[];
    timings: Record<string, number>;
    total_vids: number;
    hydrated: number;
    steps_detail?: Array<{
      step: string;
      engine: string;
      elapsed: number;
      count: number;
    }>;
    truncated?: boolean;
  };
  evidence_pointers: {
    matched_vids: string[];
    object_count: number;
  };
  created_at: string;
}

