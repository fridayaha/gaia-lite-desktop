import { http } from "@/utils/http";

export type EngineType = "HERMES" | "OPENCLAW" | "DIFY";

export type AgentDefinitionResponse = {
  id: string;
  name: string;
  description: string;
  avatar_color: string;
  engine_type: EngineType;
  status: "DRAFT" | "PUBLISHED";
  group_id: string;
  group_name: string;
  current_version_id: string | null;
  current_version_no: string | null;
  marketplace_status: "PRIVATE" | "LISTED";
  persona_config: Record<string, any>;
  model_settings: Record<string, any>;
  skill_config: Record<string, any>;
  memory_config: Record<string, any>;
  created_by: string;
  creator_name: string;
  instance_count: number;
  created_at: string;
  updated_at: string;
  published_at: string | null;
  has_unpublished_changes: boolean;
};

export type AgentDefinitionListResponse = {
  items: AgentDefinitionResponse[];
  total: number;
  page: number;
  page_size: number;
};

export type AgentVersionResponse = {
  id: string;
  definition_id: string;
  version_no: string;
  persona_config: Record<string, any>;
  model_config: Record<string, any>;
  skill_config: Record<string, any>;
  memory_config: Record<string, any>;
  engine_type: EngineType;
  change_log: string;
  created_by: string;
  created_at: string;
};

export const getDefinitionsApi = (params?: Record<string, any>) => {
  return http.request<AgentDefinitionListResponse>("get", "/api/manager/agent-definitions", {
    params
  });
};

export const createDefinitionApi = (data: {
  name: string;
  group_id: string;
  description?: string;
  avatar_color?: string;
  engine_type?: EngineType;
  persona_config?: Record<string, any>;
  model_settings?: Record<string, any>;
  skill_config?: Record<string, any>;
  memory_config?: Record<string, any>;
}) => {
  return http.request<AgentDefinitionResponse>("post", "/api/manager/agent-definitions", { data });
};

export const getDefinitionApi = (id: string) => {
  return http.request<AgentDefinitionResponse>("get", `/api/manager/agent-definitions/${id}`);
};

export const updateDefinitionApi = (id: string, data: Record<string, any>) => {
  return http.request<AgentDefinitionResponse>("put", `/api/manager/agent-definitions/${id}`, {
    data
  });
};

export const deleteDefinitionApi = (id: string) => {
  return http.request<any>("delete", `/api/manager/agent-definitions/${id}`);
};

// ── 版本管理 ────────────────────────────────────

export const getVersionsApi = (definitionId: string) => {
  return http.request<AgentVersionResponse[]>("get", `/api/manager/agent-definitions/${definitionId}/versions`);
};

export const publishDefinitionApi = (definitionId: string, data: { change_log?: string }) => {
  return http.request<AgentVersionResponse>(
    "post",
    `/api/manager/agent-definitions/${definitionId}/publish`,
    { data }
  );
};
