/**
 * Pipeline Builder 前端类型定义（与后端 pipeline_builder.py schema 对齐）。
 *
 * ADR-018 D10：graph (nodes+edges) 是单一真相源，表单/JSON/布局都是派生视图。
 */

// ── IR 核心类型 ──

/** 模式字段描述。 */
export interface SchemaField {
  name: string;
  data_type: string;
  nullable: boolean;
  description: string;
  primary_key: boolean;
}

/** 输入/输出 Schema。 */
export interface Schema {
  fields: SchemaField[];
}

/** 节点端口描述。 */
export interface NodePort {
  id: string;
  label: string;
  schema_field: string | null;
}

/** 输入契约（Schema 引擎在校验时评估）。 */
export interface InputContract {
  min_inputs: number;
  max_inputs: number;
  required_fields: string[];
  field_type_requirements: Record<string, string[]>;
}

/** 数据质量规则。 */
export interface QualityRule {
  rule_type: 'not_null' | 'unique' | 'range' | 'regex' | 'expression';
  field: string;
  config: Record<string, unknown>;
  severity: 'ERROR' | 'WARNING' | 'SPLIT';
  message: string;
}

/** 单个 JOIN 关联条件（等值连接）：左表列 = 右表列。 */
export interface JoinCondition {
  left_column: string;
  right_column: string;
}

/** 排序键：列名 + 方向。 */
export interface SortKey {
  column: string;
  direction: 'ASC' | 'DESC';
}

/** 结构化过滤条件（替代手写 SQL WHERE）。 */
export interface FilterCondition {
  column: string;
  operator:
    | 'eq' | 'neq' | 'gt' | 'gte' | 'lt' | 'lte'
    | 'in' | 'not_in' | 'is_null' | 'is_not_null'
    | 'contains' | 'not_contains' | 'starts_with' | 'ends_with';
  value?: unknown;
  values?: unknown[];
}

/** 节点配置（算子类型特定字段）。 */
export interface NodeConfig {
  expression?: string | null;
  columns?: string[] | null;
  column_mapping?: Record<string, string> | null;
  target_type?: string | null;
  cast_columns?: Array<{ column: string; target_type: string }> | null;
  join_type?: 'INNER' | 'LEFT' | 'RIGHT' | 'FULL' | null;
  join_condition?: string | null;
  join_conditions?: JoinCondition[] | null;
  group_by?: string[] | null;
  aggregations?: Array<{ field: string; function: string; alias?: string }> | null;
  sort_keys?: SortKey[] | null;
  filter_conditions?: FilterCondition[] | null;
  kestra_task_type?: string | null;
  kestra_task_config?: Record<string, unknown> | null;
  quality_rules?: QualityRule[] | null;
  extra?: Record<string, unknown>;
}

/** IR 节点类型。 */
export type NodeType = 'Source' | 'Transform' | 'Sink' | 'QualityCheck' | 'GenericKestraTask';

/** 算子类型（与后端 SchemaInferenceEngine registry 完全一致 — 单一真相源）。 */
export type OperatorType =
  | 'Source'
  | 'Sink'
  | 'Filter'
  | 'Select'
  | 'Rename'
  | 'TypeCast'
  | 'Join'
  | 'Aggregate'
  | 'Union'
  | 'Expression'
  | 'Deduplicate'
  | 'Sort'
  | 'QualityCheck'
  | 'GenericKestraTask';

/** IR 节点。 */
export interface IRNode {
  id: string;
  type: NodeType;
  operator_type: OperatorType | '';
  label: string;
  description: string;
  input_schemas: Schema[];
  output_schema: Schema | null;
  config: NodeConfig;
  position: { x: number; y: number };
  /** React Flow 运行时测量尺寸（前端用，不序列化到后端）。v12 EdgeRenderer 依赖此字段渲染边。 */
  measured?: { width: number; height: number };
}

/** IR 边。 */
export interface IREdge {
  id: string;
  source_id: string;
  target_id: string;
  source_port: string;
  target_port: string;
  /** 选中态（受控模式需回写，驱动 DeletableEdge 删除按钮显示）*/
  selected?: boolean;
}

/** 完整的 Pipeline IR 对象（引擎无关的逻辑 DAG）。 */
export interface PipelineIR {
  nodes: IRNode[];
  edges: IREdge[];
  write_mode: 'FULL_REFRESH' | 'APPEND';
  trigger_index_sync: boolean;
  tags: string[];
  owner: string | null;
  business_domain: string | null;
}

// ── API DTO ──

export interface PipelineCreate {
  api_name: string;
  display_name: string;
  description?: string;
  write_mode?: 'FULL_REFRESH' | 'APPEND';
  sink_dataset_api_name: string;
  graph?: PipelineIR;
  change_summary?: string;
}

export interface PipelineUpdate {
  display_name?: string;
  description?: string;
  write_mode?: 'FULL_REFRESH' | 'APPEND';
  sink_dataset_api_name?: string;
  graph?: PipelineIR;
  change_summary?: string;
}

export type PipelineStatus = 'DRAFT' | 'PUBLISHED' | 'DEPRECATED' | 'ARCHIVED';

export interface PipelineResponse {
  api_name: string;
  display_name: string;
  description: string;
  status: PipelineStatus;
  current_version_id: string | null;
  current_version_number: number | null;
  write_mode: 'FULL_REFRESH' | 'APPEND';
  sink_dataset_api_name: string;
  owner_id: string | null;
  project_id: string | null;
  deleted_at: string | null;
  created_at: string;
  updated_at: string;
}

export interface PipelineListResponse {
  items: PipelineResponse[];
  total: number;
  offset: number;
  limit: number;
}

export interface PipelineVersionResponse {
  id: string;
  pipeline_id: string;
  version_number: number;
  graph: PipelineIR;
  inferred_schema: Record<string, unknown> | null;
  change_summary: string;
  created_by: string | null;
  created_at: string;
}

// ── Deploy & Build ──

export interface DeployRequest {
  version_id?: string | null;
  force?: boolean;
}

export interface DeployResponse {
  api_name: string;
  status: 'DRAFT' | 'PUBLISHED';
  deployed_version_id: string;
  deployed_version_number: number;
  kestra_flow_id: string | null;
  kestra_namespace: string | null;
  deployed_at: string;
}

export interface BuildRequest {
  version_id?: string | null;
  force_build?: boolean;
  retry_count?: number;
  retry_backoff_seconds?: number;
  timeout_minutes?: number;
  abort_on_failure?: boolean;
  idempotency_key?: string | null;
}

export type BuildStatus = 'PENDING' | 'RUNNING' | 'SUCCESS' | 'FAILED' | 'CANCELLED';

export interface BuildResponse {
  build_id: string;
  pipeline_api_name: string;
  version_id: string;
  version_number: number;
  status: BuildStatus;
  trigger_type: 'MANUAL' | 'SCHEDULE' | 'UPSTREAM_EVENT';
  triggered_by: string | null;
  started_at: string | null;
  finished_at: string | null;
  duration_ms: number | null;
  error_message: string | null;
  created_at: string;
}

export type NodeRunStatus = 'PENDING' | 'RUNNING' | 'SUCCESS' | 'FAILED' | 'SKIPPED';

export interface NodeRunResponse {
  node_id: string;
  node_type: string;
  engine: string | null;
  status: NodeRunStatus;
  started_at: string | null;
  finished_at: string | null;
  duration_ms: number | null;
  error_message: string | null;
  attempt: number;
  rows_in: number | null;
  rows_out: number | null;
  bytes_processed: number | null;
}

export interface StateHistoryResponse {
  from_state: string | null;
  to_state: string;
  reason: string | null;
  changed_by: string | null;
  changed_at: string;
}

export interface BuildDetailResponse {
  build_id: string;
  pipeline_api_name: string;
  version_id: string;
  version_number: number;
  status: string;
  trigger_type: string;
  triggered_by: string | null;
  started_at: string | null;
  finished_at: string | null;
  duration_ms: number | null;
  error_message: string | null;
  output_snapshot_id: string | null;
  execution_meta: Record<string, unknown>;
  node_runs: NodeRunResponse[];
  state_history: StateHistoryResponse[];
  created_at: string;
}

// ── Validation ──

export interface ContractViolation {
  node_id: string;
  valid: boolean;
  level: 'ERROR' | 'WARNING' | 'INFO';
  message: string;
}

export interface ValidationResponse {
  valid: boolean;
  inferred_schema: Schema | null;
  contracts: ContractViolation[];
  /** 每个节点的输出 Schema（node_id → Schema），供前端配置面板渲染列下拉。 */
  node_schemas?: Record<string, Schema>;
}

// ── Schedules ──

export interface TriggerConfig {
  type: 'time' | 'webhook';
  cron?: string | null;
  tz?: string;
  key?: string | null;
}

export interface ActionConfig {
  force_build?: boolean;
  retry_count?: number;
  timeout_minutes?: number;
  abort_on_failure?: boolean;
}

export interface ScheduleCreate {
  api_name: string;
  display_name?: string;
  trigger: TriggerConfig;
  action_config?: ActionConfig;
  enabled?: boolean;
}

export interface ScheduleResponse {
  id: string;
  pipeline_api_name: string;
  api_name: string;
  display_name: string;
  trigger: TriggerConfig;
  action_config: ActionConfig;
  enabled: boolean;
  created_by: string | null;
  project_id: string | null;
  created_at: string;
  updated_at: string;
}

// ── Operator Catalog ──

export interface OperatorSpecResponse {
  type: string;
  category: 'source' | 'transform' | 'sink' | 'quality' | 'kestra';
  display_name: string;
  description: string;
  input_ports: number;
  output_ports: number;
  config_schema: Record<string, unknown>;
  output_schema_rule: string;
}

export interface OperatorCatalogResponse {
  operators: OperatorSpecResponse[];
  kestra_plugins: Record<string, unknown>[];
}

// ── React Flow 节点数据 ──

/** React Flow 节点上挂载的附加数据。 */
export interface PipelineNodeData {
  /** IR node id（与 React Flow node id 一致）。 */
  irNodeId: string;
  label: string;
  nodeType: NodeType;
  operatorType: OperatorType | '';
  /** Schema 校验状态（实时更新）。 */
  validationStatus: 'unknown' | 'valid' | 'warning' | 'error';
  validationMessages: string[];
  /** 输出的 Schema 摘要（给用户预览）。 */
  outputSchemaSummary: string;
  /** 节点配置摘要（1-3 行，画布上直接展示核心配置，避免只能看到图标+名称）。
   *  由 nodeConfigSummary.getConfigSummary 纯函数推导，每行 { text, primary }。 */
  configSummary: Array<{ text: string; primary?: boolean }>;
  /** 是否在构建中。 */
  isRunning: boolean;
  /** 节点是否可配置（用户双击编辑）。 */
  configurable: boolean;
  /** 索引签名：满足 React Flow Node<data> 约束。 */
  [key: string]: unknown;
}

// ── Pipeline Builder 面板状态 ──

export type PanelTab = 'schema' | 'history' | 'schedule' | 'monitor' | 'json';
