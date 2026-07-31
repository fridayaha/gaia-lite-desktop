// API client for Gaia backend
import type {
  Ontology,
  OntologyCreate,
  OntologyUpdate,
  ObjectType,
  ObjectTypeCapabilities,
  ObjectTypeSummary,
  PropertyDef,
  PropertyDefCreate,
  BackingColumnRef,
  LinkTypeDef,
  LinkTypeDefCreate,
  ActionTypeRecord,
  ActionTypeCreatePayload,
  ActionExecutionRequest,
  ActionExecutionResult,
  ActionPreviewResult,
  ActionTypeVersion,
  BatchActionRequest,
  BatchActionResult,
  DataSource,
  DataSourceCreate,
  DataSourceUpdate,
  Credential,
  CredentialCreate,
  SyncTask,
  SyncTaskCreate,
  DatasetGovernance,
  DatasetOntologyRef,
  PaginatedDatasets,
  ExploreResult,
  ImpactAnalysis,
  ImpactReport,
  ConnectionTestResult,
  DataSourceDef,
  TableInfo,
  LoadedObject,
} from '../types';
import type {
  PipelineCreate,
  PipelineUpdate,
  PipelineResponse,
  PipelineListResponse,
  PipelineVersionResponse,
  PipelineIR,
  DeployRequest,
  DeployResponse,
  BuildRequest,
  BuildResponse,
  BuildDetailResponse,
  ValidationResponse,
  ScheduleCreate,
  ScheduleResponse,
  OperatorCatalogResponse,
} from '../types/pipeline';

const BASE = '/ontologies';
/** Action 定义/执行路由前缀（后端 routes/action，prefix=/actions）。 */
const ACTIONS = '/actions';

/** 带 HTTP 状态码 + 后端错误码的错误，便于调用方区分 404/409/422/502 等。
 *
 * 后端统一错误响应格式：
 *   { detail: string, error_type: string, code: string }
 * 例如数据源不可达：{ detail: "无法连接到数据源…", error_type: "DataSourceUnreachableError", code: "DATASOURCE_UNREACHABLE" }
 * 解析失败（非 JSON）时 code/detail 退化为 undefined，message 退化为原始 body。 */
export class ApiError extends Error {
  status: number;
  /** 后端稳定错误码，如 DATASOURCE_UNREACHABLE / TRINO_UNAVAILABLE / OBJECT_NOT_FOUND */
  code?: string;
  /** 后端可读错误详情（面向用户） */
  detail?: string;
  /** 后端异常类型名，如 DataSourceUnreachableError */
  errorType?: string;
  constructor(message: string, status: number, opts?: { code?: string; detail?: string; errorType?: string }) {
    super(message);
    this.name = 'ApiError';
    this.status = status;
    this.code = opts?.code;
    this.detail = opts?.detail;
    this.errorType = opts?.errorType;
  }
}

/** JWT token holder — set by the app on startup/sign-in, read by every API
 *  request. Kept separate from auth-client.ts to avoid a circular import
 *  (client.ts ← auth-client.ts ← App.tsx). The auth module writes here. */
import { getJwt, clearJwt as _clearJwt } from '../lib/jwt-store';
import { AUTH_ENABLED } from '../lib/auth-client';

/** Dev fallback principal (mirrors backend AUTHZ_DEV_MODE X-User-* headers).
 *  Used only when AUTH_ENABLED is false — lets the UI run without Better Auth. */
const DEV_USER = {
  id: 'dev-admin',
  email: 'dev@gaia.local',
  name: 'Dev Admin',
  role: 'PLATFORM_ADMIN',
} as const;

/** @deprecated Register is now a no-op — jwt-store reads synchronously. */
export function registerTokenProvider(
  _getter: () => string | null,
  _clearer: () => void,
): void {
  // No-op: jwt-store is the single source of truth now.
}

/** Build the headers for a Gaia API request, merging auth + caller headers.
 *
 *  Attaches the Better Auth JWT as `Authorization: Bearer <jwt>` when one is
 *  available (ADR-016/017 Phase 5). In dev fallback (no Better Auth), no
 *  Authorization header is sent — the backend resolves the principal from
 *  X-User-Id headers (or anonymous). Preserves a caller's explicit
 *  Authorization header if set, and merges Content-Type defaults.
 *
 *  Also exported for the SSE/ai.ts fetch calls that don't go through
 *  `request()`.
 *
 *  JWT is read synchronously from the shared `jwt-store` (localStorage +
 *  in-memory cache). This is the Better Auth Bearer plugin pattern (docs
 *  §5) — no async, no effects, no race conditions. */
function withAuthHeaders(
  headers: Record<string, string> = {},
): Record<string, string> {
  const merged: Record<string, string> = { 'Content-Type': 'application/json' };
  // Inject JWT if available and not explicitly overridden.
  if (!headers.Authorization && !headers.authorization) {
    const jwt = getJwt();
    if (jwt) merged.Authorization = `Bearer ${jwt}`;
  }
  // Dev fallback: when Better Auth is disabled, the backend (AUTHZ_DEV_MODE)
  // resolves principals from X-User-* headers. Inject a default dev admin
  // so the UI is usable without Better Auth (mirrors useAuth dev session).
  // Callers may override by setting these headers explicitly.
  if (!AUTH_ENABLED) {
    if (!merged['X-User-Id'] && !headers['X-User-Id']) {
      merged['X-User-Id'] = DEV_USER.id;
    }
    if (!merged['X-User-Roles'] && !headers['X-User-Roles']) {
      merged['X-User-Roles'] = DEV_USER.role;
    }
  }
  return { ...merged, ...headers };
}

/** `fetch` wrapper that attaches the auth JWT. Use for non-JSON / SSE calls
 *  (e.g. ai.ts streaming) where `request()` doesn't fit. */
export async function authFetch(
  url: string,
  options: RequestInit = {},
): Promise<Response> {
  const headers = withAuthHeaders(
    options.headers as Record<string, string> | undefined,
  );
  return fetch(url, { ...options, headers });
}

export async function request<T>(url: string, options?: RequestInit): Promise<T> {
  const headers = withAuthHeaders(options?.headers as Record<string, string> | undefined);
  const res = await fetch(url, { ...options, headers });
  if (res.status === 204) return undefined as T;
  if (res.status === 401) {
    // JWT expired/invalid — clear it so the next request is anonymous and
    // the auth guard redirects to login. Avoids a stuck 401 loop.
    _clearJwt();
  }
  if (!res.ok) {
    const body = await res.text();
    // 解析后端统一错误格式 { detail, error_type, code }，提取结构化字段供调用方分类提示
    let code: string | undefined;
    let detail: string | undefined;
    let errorType: string | undefined;
    let message = body || `${res.status} ${res.statusText}`;
    try {
      const parsed = JSON.parse(body);
      if (parsed && typeof parsed === 'object') {
        code = parsed.code;
        detail = parsed.detail;
        errorType = parsed.error_type;
        message = parsed.detail || body;
      }
    } catch {
      // 非 JSON 响应（如代理 502），保留原始 body
    }
    throw new ApiError(message, res.status, { code, detail, errorType });
  }
  return res.json();
}

// ── Ontology ──

export function listOntologies(
  includeDeleted = false,
  includeDeprecated = false,
): Promise<Ontology[]> {
  const params = new URLSearchParams();
  if (includeDeleted) params.set('include_deleted', 'true');
  if (includeDeprecated) params.set('include_deprecated', 'true');
  const qs = params.toString() ? `?${params.toString()}` : '';
  return request<Ontology[]>(`${BASE}${qs}`);
}

export function getOntology(apiName: string, includeDeleted = false): Promise<Ontology> {
  const qs = includeDeleted ? '?include_deleted=true' : '';
  return request<Ontology>(`${BASE}/${apiName}${qs}`);
}

export function createOntology(data: OntologyCreate): Promise<Ontology> {
  return request<Ontology>(BASE, { method: 'POST', body: JSON.stringify(data) });
}

export function updateOntology(apiName: string, data: OntologyUpdate): Promise<Ontology> {
  return request<Ontology>(`${BASE}/${apiName}`, { method: 'PATCH', body: JSON.stringify(data) });
}

export function deleteOntology(apiName: string): Promise<void> {
  return request<void>(`${BASE}/${apiName}`, { method: 'DELETE' });
}

/** v5.2: reverse a soft-delete. Physical resources (Doris idx / INDEX pipeline) are NOT re-provisioned. */
export function restoreOntology(apiName: string): Promise<Ontology> {
  return request<Ontology>(`${BASE}/${apiName}/restore`, { method: 'POST' });
}

/** v5.2: cascade-impact report for the delete confirm dialog. */
export function getOntologyImpact(apiName: string): Promise<ImpactReport> {
  return request<ImpactReport>(`${BASE}/${apiName}/impact`);
}

/** v5.2: Deprecate an ontology (ACTIVE → DEPRECATED), the precondition for delete. */
export function deprecateOntology(apiName: string): Promise<Ontology> {
  return updateOntology(apiName, { status: 'DEPRECATED' });
}

// ── ObjectType ──

export function listObjectTypes(ontologyName: string): Promise<ObjectType[]> {
  return request<ObjectType[]>(`${BASE}/${ontologyName}/object-types`);
}

/** Lightweight list — no property details, suitable for sidebar/table/canvas. */
export function listObjectTypeSummaries(ontologyName: string): Promise<ObjectTypeSummary[]> {
  return request<ObjectTypeSummary[]>(`${BASE}/${ontologyName}/object-types/summary`);
}

export function getObjectType(ontologyName: string, typeName: string): Promise<ObjectType> {
  return request<ObjectType>(`${BASE}/${ontologyName}/object-types/${typeName}`);
}

// Batch create ObjectType with properties and links (single transaction)
export interface ObjectTypeBatchPayload {
  /** apiName: PascalCase, caller-supplied (frontend LLM-derives, user confirms). */
  api_name: string;
  display_name: string;
  description?: string;
  /** 可选：省略时由后端从属性的 is_primary_key 反推。 */
  primary_key?: string;
  /** 可选：省略时由后端从属性的 is_title_property 反推。 */
  title_property?: string;
  storage_type: string;
  properties: {
    /** apiName 由后端从 display_name/backing_column 推导 (camelCase)。 */
    display_name: string;
    /** 业务语义说明 (LLM-facing)。 */
    description?: string;
    data_type: string;
    searchable?: boolean;
    is_primary_key?: boolean;
    is_title_property?: boolean;
    backing_mapping?: BackingColumnRef | null;
  }[];
  links: {
    /** apiName 由后端从 display_name 推导 (camelCase)。 */
    display_name: string;
    target_object_type_id: string;
    cardinality: string;
    direction: string;
  }[];
}

export function createObjectTypeBatch(
  ontologyName: string,
  data: ObjectTypeBatchPayload,
): Promise<ObjectType> {
  return request<ObjectType>(`${BASE}/${ontologyName}/object-types/create`, {
    method: 'POST',
    body: JSON.stringify(data),
  });
}

export function updateObjectType(
  ontologyName: string,
  typeName: string,
  updates: Record<string, unknown>,
): Promise<ObjectType> {
  return request<ObjectType>(`${BASE}/${ontologyName}/object-types/${typeName}`, {
    method: 'PATCH',
    body: JSON.stringify(updates),
  });
}

/** Update an ObjectType's capabilities (graph/geotime indexing opt-in). */
export function updateObjectTypeCapabilities(
  ontologyName: string,
  typeName: string,
  capabilities: ObjectTypeCapabilities,
): Promise<ObjectType> {
  return updateObjectType(ontologyName, typeName, { capabilities });
}

export function deleteObjectType(ontologyName: string, typeName: string): Promise<void> {
  return request<void>(`${BASE}/${ontologyName}/object-types/${typeName}`, {
    method: 'DELETE',
  });
}

/** A1 — bind an ObjectType's properties to a Dataset's columns. */
export function linkDataset(
  ontologyName: string,
  typeName: string,
  datasetApiName: string,
  columnMappings: { property_api_name: string; column_name: string }[],
): Promise<ObjectType> {
  return request<ObjectType>(`${BASE}/${ontologyName}/object-types/${typeName}/dataset-link`, {
    method: 'PATCH',
    body: JSON.stringify({ dataset_api_name: datasetApiName, column_mappings: columnMappings }),
  });
}

/** A1 — clear dataset links. Pass property api_names to clear selectively. */
export function unlinkDataset(
  ontologyName: string,
  typeName: string,
  propertyApiNames?: string[],
): Promise<ObjectType> {
  const qs =
    propertyApiNames && propertyApiNames.length
      ? '?' + propertyApiNames.map((n) => `property_api_names=${encodeURIComponent(n)}`).join('&')
      : '';
  return request<ObjectType>(`${BASE}/${ontologyName}/object-types/${typeName}/dataset-link${qs}`, {
    method: 'DELETE',
  });
}

/** Batch update an ObjectType (properties + links, single transaction). */
export function updateObjectTypeBatch(
  ontologyName: string,
  typeName: string,
  data: Record<string, unknown>,
): Promise<ObjectType> {
  return request<ObjectType>(`${BASE}/${ontologyName}/object-types/${typeName}/batch`, {
    method: 'PATCH',
    body: JSON.stringify(data),
  });
}

// ── Property ──

export function addProperty(
  ontologyName: string,
  typeName: string,
  data: PropertyDefCreate,
): Promise<PropertyDef> {
  return request<PropertyDef>(`${BASE}/${ontologyName}/object-types/${typeName}/properties`, {
    method: 'POST',
    body: JSON.stringify(data),
  });
}

export function listProperties(ontologyName: string, typeName: string): Promise<PropertyDef[]> {
  return request<PropertyDef[]>(`${BASE}/${ontologyName}/object-types/${typeName}/properties`);
}

export function deleteProperty(
  ontologyName: string,
  typeName: string,
  propertyName: string,
): Promise<void> {
  return request<void>(
    `${BASE}/${ontologyName}/object-types/${typeName}/properties/${propertyName}`,
    { method: 'DELETE' },
  );
}

// ── LinkType ──

export function createLinkType(
  ontologyName: string,
  data: LinkTypeDefCreate,
): Promise<LinkTypeDef> {
  return request<LinkTypeDef>(`${BASE}/${ontologyName}/link-types`, {
    method: 'POST',
    body: JSON.stringify(data),
  });
}

export function listLinkTypes(ontologyName: string): Promise<LinkTypeDef[]> {
  return request<LinkTypeDef[]>(`${BASE}/${ontologyName}/link-types`);
}

export function deleteLinkType(ontologyName: string, linkName: string): Promise<void> {
  return request<void>(`${BASE}/${ontologyName}/link-types/${linkName}`, {
    method: 'DELETE',
  });
}

// ── ActionType ──

export function listActionTypes(ontologyName: string): Promise<ActionTypeRecord[]> {
  return request<ActionTypeRecord[]>(`${BASE}/${ontologyName}/action-types`);
}

/** Execute an action against an object type. */
export function executeAction(
  ontology: string,
  objectType: string,
  action: string,
  payload: ActionExecutionRequest,
): Promise<ActionExecutionResult> {
  return request<ActionExecutionResult>(`${ACTIONS}/execute/${ontology}/${objectType}/${action}`, {
    method: 'POST',
    body: JSON.stringify(payload),
  });
}

/** P2: Execute an action against a batch of objects (Batch Action). */
export function executeBatchAction(
  ontology: string,
  objectType: string,
  action: string,
  payload: BatchActionRequest,
): Promise<BatchActionResult> {
  return request<BatchActionResult>(`${ACTIONS}/execute-batch/${ontology}/${objectType}/${action}`, {
    method: 'POST',
    body: JSON.stringify(payload),
  });
}

/** P1 (ADR-011): dry-run preview an action without persisting. */
export function previewAction(
  ontology: string,
  objectType: string,
  action: string,
  payload: ActionExecutionRequest,
): Promise<ActionPreviewResult> {
  return request<ActionPreviewResult>(`${ACTIONS}/preview/${ontology}/${objectType}/${action}`, {
    method: 'POST',
    body: JSON.stringify(payload),
  });
}

/** ADR Action Mutation Mapping: define a new ActionType (POST /actions/definitions). */
export function defineActionType(
  ontology: string,
  actionType: string,
  body: ActionTypeCreatePayload,
): Promise<ActionTypeRecord> {
  return request<ActionTypeRecord>(`${ACTIONS}/definitions/${ontology}/${actionType}`, {
    method: 'POST',
    body: JSON.stringify(body),
  });
}

/** ADR Action Mutation Mapping: fetch a single ActionType definition (编辑回填). */
export function getActionType(ontology: string, actionType: string): Promise<ActionTypeRecord> {
  return request<ActionTypeRecord>(`${ACTIONS}/definitions/${ontology}/${actionType}`);
}

/** ADR Action Mutation Mapping: soft-delete an ActionType (status=DEPRECATED). */
export function deleteActionType(ontology: string, actionType: string): Promise<void> {
  return request<void>(`${ACTIONS}/definitions/${ontology}/${actionType}`, { method: 'DELETE' });
}

/** P1 (ADR-011): update an ActionType and publish a new version. */
export function updateActionType(
  ontology: string,
  actionType: string,
  updates: Record<string, unknown>,
): Promise<ActionTypeRecord> {
  return request<ActionTypeRecord>(`${ACTIONS}/definitions/${ontology}/${actionType}`, {
    method: 'PATCH',
    body: JSON.stringify(updates),
  });
}

/** P1 (ADR-011): list historical versions of an ActionType. */
export function listActionTypesVersions(
  ontology: string,
  actionType: string,
): Promise<ActionTypeVersion[]> {
  return request<ActionTypeVersion[]>(`${ACTIONS}/definitions/${ontology}/${actionType}/versions`);
}

/** P1 (ADR-011): roll back an ActionType to a prior version. */
export function rollbackActionType(
  ontology: string,
  actionType: string,
  version: number,
): Promise<ActionTypeRecord> {
  return request<ActionTypeRecord>(
    `${ACTIONS}/definitions/${ontology}/${actionType}/rollback/${version}`,
    { method: 'POST' },
  );
}

// ═══════════════════════════════════════════════════════════
// Data Layer — DataSource / Credential / SyncTask / Dataset
// ═══════════════════════════════════════════════════════════

const DATA_API = '/api';

// ── Credentials ──

export function createCredential(data: CredentialCreate): Promise<Credential> {
  return request<Credential>(`${DATA_API}/credentials`, {
    method: 'POST',
    body: JSON.stringify(data),
  });
}

export function listCredentials(): Promise<Credential[]> {
  return request<Credential[]>(`${DATA_API}/credentials`);
}

export function deleteCredential(apiName: string): Promise<void> {
  return request<void>(`${DATA_API}/credentials/${apiName}`, { method: 'DELETE' });
}

// ── DataSources ──

export function createDataSource(data: DataSourceCreate): Promise<DataSource> {
  return request<DataSource>(`${DATA_API}/datasources`, {
    method: 'POST',
    body: JSON.stringify(data),
  });
}

export function listDataSources(): Promise<DataSource[]> {
  return request<DataSource[]>(`${DATA_API}/datasources`);
}

export function getDataSource(apiName: string): Promise<DataSource> {
  return request<DataSource>(`${DATA_API}/datasources/${apiName}`);
}

export function updateDataSource(apiName: string, data: DataSourceUpdate): Promise<DataSource> {
  return request<DataSource>(`${DATA_API}/datasources/${apiName}`, {
    method: 'PATCH',
    body: JSON.stringify(data),
  });
}

export function deleteDataSource(apiName: string): Promise<void> {
  return request<void>(`${DATA_API}/datasources/${apiName}`, { method: 'DELETE' });
}

export function testConnection(apiName: string): Promise<ConnectionTestResult> {
  return request<ConnectionTestResult>(`${DATA_API}/datasources/${apiName}/test-connection`, {
    method: 'POST',
  });
}

export function exploreDataSource(apiName: string, database?: string): Promise<ExploreResult> {
  return request<ExploreResult>(`${DATA_API}/datasources/${apiName}/explore`, {
    method: 'POST',
    body: JSON.stringify({ database: database || '' }),
  });
}

/** Describe a single table's columns — lazy-loaded on user click. */
export function describeTable(
  apiName: string,
  database: string,
  table: string,
): Promise<TableInfo> {
  return request<TableInfo>(`${DATA_API}/datasources/${apiName}/explore/${database}/${table}`, {
    method: 'POST',
  });
}

/** Sample rows from a specific table in a data source. */
export function sampleData(
  apiName: string,
  database: string,
  table: string,
  limit?: number,
): Promise<Record<string, unknown>[]> {
  return request<Record<string, unknown>[]>(
    `${DATA_API}/datasources/${apiName}/explore/${database}/${table}/sample?limit=${limit || 20}`,
  );
}

/** Register an external table as a kind=VIRTUAL dataset (F0). */
export function registerVirtualTable(
  datasourceApiName: string,
  data: { database: string; table: string; api_name?: string; display_name?: string },
): Promise<DatasetGovernance> {
  return request<DatasetGovernance>(`${DATA_API}/datasources/${datasourceApiName}/virtual-tables`, {
    method: 'POST',
    body: JSON.stringify(data),
  });
}

// ── SyncTasks ──

export function createSyncTask(datasourceApiName: string, data: SyncTaskCreate): Promise<SyncTask> {
  return request<SyncTask>(`${DATA_API}/datasources/${datasourceApiName}/sync-tasks`, {
    method: 'POST',
    body: JSON.stringify(data),
  });
}

export function listSyncTasks(datasourceApiName: string): Promise<SyncTask[]> {
  return request<SyncTask[]>(`${DATA_API}/datasources/${datasourceApiName}/sync-tasks`);
}

/**
 * Batch-reconcile ALL of a datasource's sync tasks with SeaTunnel in 2
 * backend calls (instead of N per-task refresh calls). Use this when
 * loading a sync task list to avoid request storms on datasources with
 * many tasks. Returns the refreshed tasks (PG-truth after reconcile).
 */
export function refreshAllSyncTasks(datasourceApiName: string): Promise<SyncTask[]> {
  return request<SyncTask[]>(
    `${DATA_API}/datasources/${datasourceApiName}/sync-tasks/refresh-batch`,
    { method: 'POST' },
  );
}

export function getSyncTask(apiName: string): Promise<SyncTask> {
  return request<SyncTask>(`${DATA_API}/sync-tasks/${apiName}`);
}

export function startSyncTask(apiName: string): Promise<SyncTask> {
  return request<SyncTask>(`${DATA_API}/sync-tasks/${apiName}/start`, { method: 'POST' });
}

export function stopSyncTask(apiName: string): Promise<SyncTask> {
  return request<SyncTask>(`${DATA_API}/sync-tasks/${apiName}/stop`, { method: 'POST' });
}

export function refreshSyncTask(apiName: string): Promise<SyncTask> {
  // Poll SeaTunnel for the job's real state and persist it. Call this
  // when rendering a sync task list/detail so the UI doesn't show a
  // phantom "已完成" based on a blindly-written RUNNING status.
  return request<SyncTask>(`${DATA_API}/sync-tasks/${apiName}/refresh`, { method: 'POST' });
}

export function deleteSyncTask(apiName: string): Promise<void> {
  return request<void>(`${DATA_API}/sync-tasks/${apiName}`, { method: 'DELETE' });
}

// ── Datasets ──

export function registerDataset(data: Record<string, unknown>): Promise<DatasetGovernance> {
  return request<DatasetGovernance>(`${DATA_API}/datasets`, {
    method: 'POST',
    body: JSON.stringify(data),
  });
}

export function listDatasets(): Promise<DatasetGovernance[]> {
  return request<DatasetGovernance[]>(`${DATA_API}/datasets`);
}

/** Paginated dataset list for the datasets management page. */
export function listDatasetsPaginated(params: {
  page: number;
  pageSize: number;
  search?: string;
  type?: string;
  ontology?: string;
}): Promise<PaginatedDatasets> {
  const qs = new URLSearchParams({
    page: String(params.page),
    page_size: String(params.pageSize),
  });
  if (params.search) qs.set('search', params.search);
  if (params.type) qs.set('type', params.type);
  if (params.ontology) qs.set('ontology', params.ontology);
  return request<PaginatedDatasets>(`${DATA_API}/datasets/paginated?${qs}`);
}

/** Reverse-lookup map: dataset api_name -> referencing ontologies. */
export function getDatasetOntologyMap(): Promise<Record<string, DatasetOntologyRef[]>> {
  return request<Record<string, DatasetOntologyRef[]>>(`${DATA_API}/datasets/ontology-map`);
}

/** Refresh a dataset's row_count_estimate via Trino (Iceberg for MANAGED, federation for VIRTUAL). */
export function refreshDatasetStats(apiName: string): Promise<DatasetGovernance> {
  return request<DatasetGovernance>(`${DATA_API}/datasets/${apiName}/refresh-stats`, { method: 'POST' });
}

// ═══════════════════════════════════════════════════════════════════
// Pipeline Builder — ADR-018
// ═══════════════════════════════════════════════════════════════════

const PIPELINES = '/api/v1/pipelines';

export function listPipelines(params?: {
  offset?: number;
  limit?: number;
  search?: string;
  status?: string;
  project_id?: string;
}): Promise<PipelineListResponse> {
  const qs = new URLSearchParams();
  if (params?.offset !== undefined) qs.set('offset', String(params.offset));
  if (params?.limit !== undefined) qs.set('limit', String(params.limit));
  if (params?.search) qs.set('search', params.search);
  if (params?.status) qs.set('status', params.status);
  if (params?.project_id) qs.set('project_id', params.project_id);
  return request<PipelineListResponse>(`${PIPELINES}?${qs}`);
}

export function createPipeline(data: PipelineCreate): Promise<PipelineResponse> {
  return request<PipelineResponse>(PIPELINES, {
    method: 'POST',
    body: JSON.stringify(data),
  });
}

export function getPipeline(apiName: string): Promise<PipelineResponse> {
  return request<PipelineResponse>(`${PIPELINES}/${apiName}`);
}

export function updatePipeline(apiName: string, data: PipelineUpdate): Promise<PipelineResponse> {
  return request<PipelineResponse>(`${PIPELINES}/${apiName}`, {
    method: 'PATCH',
    body: JSON.stringify(data),
  });
}

export function deletePipeline(apiName: string): Promise<void> {
  return request<void>(`${PIPELINES}/${apiName}`, { method: 'DELETE' });
}

/** 获取管道所有版本历史。 */
export function listPipelineVersions(apiName: string): Promise<PipelineVersionResponse[]> {
  return request<PipelineVersionResponse[]>(`${PIPELINES}/${apiName}/versions`);
}

/** 获取指定版本。 */
export function getPipelineVersion(apiName: string, versionNumber: number): Promise<PipelineVersionResponse> {
  return request<PipelineVersionResponse>(`${PIPELINES}/${apiName}/versions/${versionNumber}`);
}

/** 保存管道（创建新版本）。调 PATCH /pipelines/{api_name} 传入 graph，
 * 后端会生成新版本号。 */
export function savePipelineVersion(
  apiName: string,
  graph: PipelineIR,
  changeSummary?: string,
): Promise<PipelineVersionResponse> {
  // 后端 PATCH 返回 PipelineResponse（含 current_version_id/number），
  // 但前端需要 PipelineVersionResponse（含完整 graph）。为了拿到完整版本对象，
  // 先 PATCH 保存，再 GET 最新版本详情。
  return updatePipeline(apiName, {
    graph,
    change_summary: changeSummary ?? '',
  }).then((p) => getPipelineVersion(apiName, p.current_version_number ?? 1));
}

/** Deploy：使某个版本成为当前生效版本，翻译为 Kestra Flow 并注册。 */
export function deployPipeline(
  apiName: string,
  req: DeployRequest,
): Promise<DeployResponse> {
  return request<DeployResponse>(`${PIPELINES}/${apiName}/deploy`, {
    method: 'POST',
    body: JSON.stringify(req),
  });
}

/** Build（执行）：物化数据。 */
export function buildPipeline(
  apiName: string,
  req?: BuildRequest,
): Promise<BuildResponse> {
  return request<BuildResponse>(`${PIPELINES}/${apiName}/builds`, {
    method: 'POST',
    body: JSON.stringify(req ?? {}),
  });
}

/** 列出构建执行记录（轻量列表，无 node_runs/state_history）。 */
export function listPipelineBuilds(
  apiName: string,
  limit?: number,
): Promise<BuildResponse[]> {
  const qs = limit ? `?limit=${limit}` : '';
  return request<BuildResponse[]>(`${PIPELINES}/${apiName}/builds${qs}`);
}

/** 获取构建执行详情（含节点级执行状态）。 */
export function getPipelineBuild(
  apiName: string,
  buildId: string,
): Promise<BuildDetailResponse> {
  return request<BuildDetailResponse>(`${PIPELINES}/${apiName}/builds/${buildId}`);
}

/** Schema 校验 + 推演（编辑时实时调用）。 */
export function validatePipeline(apiName: string): Promise<ValidationResponse> {
  return request<ValidationResponse>(`${PIPELINES}/${apiName}/validate`, { method: 'POST' });
}

/** 校验未保存的 raw graph（不需 apiName，用于编辑时实时 schema 推导）。 */
export function validateRawGraph(graph: PipelineIR): Promise<ValidationResponse> {
  return request<ValidationResponse>(`${PIPELINES}/validate`, {
    method: 'POST',
    body: JSON.stringify(graph),
  });
}

/** 获取算子目录。 */
export function getOperatorCatalog(): Promise<OperatorCatalogResponse> {
  return request<OperatorCatalogResponse>('/api/v1/pipelines/operators');
}

// ── Schedules ──

export function listPipelineSchedules(apiName: string): Promise<ScheduleResponse[]> {
  return request<ScheduleResponse[]>(`${PIPELINES}/${apiName}/schedules`);
}

export function createPipelineSchedule(
  apiName: string,
  data: ScheduleCreate,
): Promise<ScheduleResponse> {
  return request<ScheduleResponse>(`${PIPELINES}/${apiName}/schedules`, {
    method: 'POST',
    body: JSON.stringify(data),
  });
}

export function updatePipelineSchedule(
  apiName: string,
  scheduleApiName: string,
  data: Partial<ScheduleCreate>,
): Promise<ScheduleResponse> {
  return request<ScheduleResponse>(`${PIPELINES}/${apiName}/schedules/${scheduleApiName}`, {
    method: 'PATCH',
    body: JSON.stringify(data),
  });
}

export function deletePipelineSchedule(apiName: string, scheduleApiName: string): Promise<void> {
  return request<void>(`${PIPELINES}/${apiName}/schedules/${scheduleApiName}`, {
    method: 'DELETE',
  });
}

export function getDataset(apiName: string): Promise<DatasetGovernance> {
  return request<DatasetGovernance>(`${DATA_API}/datasets/${apiName}`);
}

export function updateDataset(
  apiName: string,
  data: Record<string, unknown>,
): Promise<DatasetGovernance> {
  return request<DatasetGovernance>(`${DATA_API}/datasets/${apiName}`, {
    method: 'PATCH',
    body: JSON.stringify(data),
  });
}

export function deleteDataset(apiName: string): Promise<void> {
  return request<void>(`${DATA_API}/datasets/${apiName}`, { method: 'DELETE' });
}

export function getDatasetSchema(
  apiName: string,
): Promise<{ columns: { name: string; type: string; nullable: boolean }[] }> {
  return request(`${DATA_API}/datasets/${apiName}/schema`);
}

export function getDatasetSnapshots(
  apiName: string,
): Promise<
  { snapshot_id: number; timestamp: number; operation: string; summary: Record<string, unknown> }[]
> {
  return request(`${DATA_API}/datasets/${apiName}/snapshots`);
}

// ── Impact Analysis ──

export function analyzeImpact(
  targetApiName: string,
  targetType: string,
  action: string,
): Promise<ImpactAnalysis> {
  return request<ImpactAnalysis>(`${DATA_API}/impact-analysis`, {
    method: 'POST',
    body: JSON.stringify({ target_api_name: targetApiName, target_type: targetType, action }),
  });
}

// ── Legacy DataSource (for backward compat) ──

export function createDataSourceLegacy(
  ontologyName: string,
  data: { name: string; source_type: string; config?: Record<string, unknown> },
): Promise<DataSourceDef> {
  return request<DataSourceDef>(`${BASE}/${ontologyName}/data-sources`, {
    method: 'POST',
    body: JSON.stringify(data),
  });
}

export function listDataSourcesLegacy(ontologyName: string): Promise<DataSourceDef[]> {
  return request<DataSourceDef[]>(`${BASE}/${ontologyName}/data-sources`);
}

// ── Object loading (POST /objects/textsql) ──────────────────────────

/** Load object instances of a type (for ObjectReference pickers, previews).
 *
 * 走 TextQL 编译路径（ADR-012 Step 4 path B）：拼 logical SQL
 * `SELECT <props|*> FROM <OT> LIMIT <n>`，后端 OntologySqlCompiler 做列名
 * 映射、参数化绑定、方言分叉（MANAGED→Doris / VIRTUAL→Trino 联邦）。
 * 注：原 /objects/load 手写旁路已收编删除，统一走 textsql 编译路径。
 */
export function loadObjects(
  ontology: string,
  objectType: string,
  options: { limit?: number; properties?: string[] } = {},
): Promise<LoadedObject[]> {
  const { limit = 50, properties = [] } = options;
  const selectCols = properties.length > 0 ? properties.join(', ') : '*';
  const logicalSql = `SELECT ${selectCols} FROM ${objectType} LIMIT ${limit}`;
  return request<LoadedObject[]>('/objects/textsql', {
    method: 'POST',
    body: JSON.stringify({
      ontology_api_name: ontology,
      logical_sql: logicalSql,
    }),
  });
}

/** Search objects of a type by substring across given properties.
 *
 *  Backed by /objects/textsql with a generated WHERE <prop> LIKE '%q%' OR ...
 *  clause. Used by ObjectPicker for server-side search (replaces the old
 *  load-all-50 + client-filter approach). An empty query matches all rows
 *  (no WHERE clause) — used when the picker first focuses to show the
 *  candidate set.
 *
 *  `searchProperties` defaults to [pk, title]; the caller may pass more
 *  (configured per-parameter via `search_properties`). */
export function searchObjects(
  ontology: string,
  objectType: string,
  query: string,
  searchProperties: string[],
  options: { limit?: number; properties?: string[] } = {},
): Promise<LoadedObject[]> {
  const { limit = 20, properties = [] } = options;
  const selectCols = properties.length > 0 ? properties.join(', ') : '*';
  const q = query.trim();
  let where = '';
  if (q) {
    const like = searchProperties.map((p) => `${p} LIKE '%${q.replace(/'/g, "''")}%'`).join(' OR ');
    where = ` WHERE ${like}`;
  }
  const logicalSql = `SELECT ${selectCols} FROM ${objectType}${where} LIMIT ${limit}`;
  return request<LoadedObject[]>('/objects/textsql', {
    method: 'POST',
    body: JSON.stringify({
      ontology_api_name: ontology,
      logical_sql: logicalSql,
    }),
  });
}
