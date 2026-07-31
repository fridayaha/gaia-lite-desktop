/**
 * Hub 能力中心 API — 对接契约 §5（/api/hub，独立服务 8003）。
 *
 * 注意：契约 §5 仅列出实体（hub_item / hub_item_version / approval_record /
 * scan_report / scan_finding / lifecycle_event）与生命周期（Import→Review→Publish→Discover），
 * 未冻结具体 REST 路径。此处沿用 Repo1 hub 后端已验证的路径，B 落地后若有偏差需回看对齐。
 */
import { http } from "@/utils/http";

// ── 类型 ────────────────────────────────────────────────

export type HubItemType = "agent" | "skill" | "tool" | "mcp";
export type HubItemStatus =
  | "draft"
  | "pending_review"
  | "approved"
  | "published"
  | "rejected"
  | "disabled"
  | "archived";
export type RiskLevel = "low" | "medium" | "high" | "blocking" | string;

export type HubItem = {
  id: string;
  name: string;
  type: HubItemType;
  description: string;
  status: HubItemStatus;
  risk_level?: RiskLevel;
  industry?: string;
  scenario?: string;
  created_by?: string;
  source_type?: string;
  group_id?: string;
  featured?: boolean;
  tags?: string[];
  current_version_id?: string;
  discoverable?: boolean;
  created_at?: string;
  updated_at?: string;
};

export type HubItemListResponse = {
  items: HubItem[];
  total: number;
};

export type HubItemVersion = {
  id: string;
  hub_item_id: string;
  version: string;
  status: HubItemStatus;
  risk_level?: RiskLevel;
  description?: string;
  config_json?: Record<string, any>;
  created_by?: string;
  created_at?: string;
};

export type ScanFinding = {
  id: string;
  severity: RiskLevel;
  rule_id: string;
  message: string;
  location?: string;
};

export type ScanReport = {
  id: string;
  hub_item_id: string;
  version_id?: string;
  status: "pending" | "running" | "completed" | "failed";
  risk_level: RiskLevel;
  finding_count: number;
  findings: ScanFinding[];
  scanned_at?: string;
};

// ── 能力项 CRUD ─────────────────────────────────────────

export const listHubItemsApi = (params?: Record<string, any>) => {
  return http.request<HubItemListResponse>("get", "/api/hub/items", { params });
};

export const getHubItemApi = (id: string) => {
  return http.request<HubItem>("get", `/api/hub/items/${id}`);
};

export const createHubItemApi = (data: {
  name: string;
  type: HubItemType;
  description?: string;
  industry?: string;
  scenario?: string;
  risk_level?: RiskLevel;
  featured?: boolean;
  created_by?: string;
}) => {
  return http.request<HubItem>("post", "/api/hub/items", { data });
};

export const initHubPresetsApi = () => {
  return http.request<{ created: number }>("post", "/api/hub/presets/init");
};

// ── 订阅能力到智能体模版（manager 侧） ─────────────────────
// 把 hub 的指定版本技能包安装到模版；后续由模版 publish + instance upgrade 版本化生效。

export const installFromHubApi = (
  definitionId: string,
  payload: { hub_item_id: string; version_id: string }
) => {
  return http.request<any>(
    "post",
    `/api/manager/agent-definitions/${definitionId}/skills/install-from-hub`,
    { data: payload }
  );
};

// ── 版本 + 审批工作流 ───────────────────────────────────

export const listHubVersionsApi = (itemId: string) => {
  return http.request<HubItemVersion[]>("get", `/api/hub/items/${itemId}/versions`);
};

export const createHubVersionApi = (
  itemId: string,
  data: { version: string; description?: string; config_json?: Record<string, any> }
) => {
  return http.request<HubItemVersion>("post", `/api/hub/items/${itemId}/versions`, {
    data: { hub_item_id: itemId, ...data, config_json: data.config_json || {} }
  });
};

export const submitHubReviewApi = (versionId: string, operator = "admin") => {
  return http.request<any>("post", `/api/hub/versions/${versionId}/submit-review`, {
    data: { operator }
  });
};

export const approveHubVersionApi = (versionId: string, operator = "admin") => {
  return http.request<any>("post", `/api/hub/versions/${versionId}/approve`, {
    data: { operator }
  });
};

export const rejectHubVersionApi = (versionId: string, operator = "admin", reason?: string) => {
  return http.request<any>("post", `/api/hub/versions/${versionId}/reject`, {
    data: { operator, reason }
  });
};

export const publishHubVersionApi = (versionId: string, operator = "admin") => {
  return http.request<any>("post", `/api/hub/versions/${versionId}/publish`, {
    data: { operator }
  });
};

// ── 安全扫描 ─────────────────────────────────────────────
// 注意：扫描报告按 version_id 查询（hub 后端 /api/hub/versions/{vid}/scan-report）。

export const getHubScanReportApi = (versionId: string) => {
  return http.request<ScanReport>("get", `/api/hub/versions/${versionId}/scan-report`);
};
