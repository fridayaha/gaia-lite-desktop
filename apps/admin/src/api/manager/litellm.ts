import { http } from "@/utils/http";

// ── 类型 ────────────────────────────────────────────────

export type LiteLLMModelGroup = {
  model_group: string;
  model: string;
  provider: string;
  context_length?: number | null;
};

export type LiteLLMModelDeployment = {
  model_name: string;
  litellm_params: Record<string, any>;
  model_info?: Record<string, any>;
  input_cost_per_1m_tokens?: number | null;
  output_cost_per_1m_tokens?: number | null;
};

export type LiteLLMKey = {
  key_id?: string;
  key?: string;
  token?: string;
  key_name?: string;
  key_alias?: string;
  blocked?: boolean;
  models?: string[];
  team_id?: string;
  max_budget?: number | null;
  budget_duration?: string | null;
  rpm_limit?: number | null;
  tpm_limit?: number | null;
  duration?: string | null;
  spend?: number;
  metadata?: Record<string, any>;
};

export type LiteLLMTeam = {
  group_id: string;
  name: string;
  team_id: string;
  synced: boolean;
};

export type LiteLLMKeyCreate = {
  group_id: string;
  models?: string[];
  max_budget?: number;
  budget_duration?: string;
  rpm_limit?: number;
  tpm_limit?: number;
  duration?: string;
  key_alias?: string;
};

// ── 列表响应（非分页全量列表统一 {items,total}） ────────

export type LiteLLMModelGroupListResponse = { items: LiteLLMModelGroup[]; total: number };
export type LiteLLMModelDeploymentListResponse = { items: LiteLLMModelDeployment[]; total: number };
export type LiteLLMTeamListResponse = { items: LiteLLMTeam[]; total: number };
export type LiteLLMKeyListResponse = { items: LiteLLMKey[]; total: number };

// ── 模型组（全局上游供应商） ────────────────────────────

export const getModelGroupsApi = () => {
  return http.request<LiteLLMModelGroupListResponse>("get", "/api/manager/litellm/model-groups");
};

export const getModelsApi = () => {
  return http.request<LiteLLMModelDeploymentListResponse>("get", "/api/manager/litellm/models");
};

export const createModelApi = (data: {
  model_name: string;
  model: string;
  api_key: string;
  api_base?: string;
  custom_llm_provider?: string;
  context_length?: number | null;
}) => {
  return http.request<LiteLLMModelDeployment>("post", "/api/manager/litellm/models", { data });
};

export const deleteModelApi = (modelId: string) => {
  return http.request<void>("delete", `/api/manager/litellm/models/${modelId}`);
};

export const updateModelApi = (modelId: string, data: {
  model?: string;
  api_key?: string;
  api_base?: string;
  custom_llm_provider?: string;
  context_length?: number | null;
}) => {
  return http.request<LiteLLMModelDeployment>("put", `/api/manager/litellm/models/${modelId}`, { data });
};

export const updateModelPriceApi = (modelId: string, data: {
  input_cost_per_1m_tokens: number | null;
  output_cost_per_1m_tokens: number | null;
}) => {
  return http.request<LiteLLMModelDeployment>("put", `/api/manager/litellm/models/${modelId}/price`, { data });
};

// ── Team 同步 ───────────────────────────────────────────

export const getTeamsApi = () => {
  return http.request<LiteLLMTeamListResponse>("get", "/api/manager/litellm/teams");
};

export const syncTeamsApi = () => {
  return http.request<{ synced: string[]; count: number }>("post", "/api/manager/litellm/teams/sync");
};

// ── Virtual Key ─────────────────────────────────────────

export const getKeysApi = (groupId?: string) => {
  return http.request<LiteLLMKeyListResponse>("get", "/api/manager/litellm/keys", {
    params: groupId ? { group_id: groupId } : undefined
  });
};

export const createKeyApi = (data: LiteLLMKeyCreate) => {
  return http.request<LiteLLMKey>("post", "/api/manager/litellm/keys", { data });
};

export const updateKeyApi = (keyId: string, data: Partial<LiteLLMKeyCreate>) => {
  return http.request<LiteLLMKey>("put", `/api/manager/litellm/keys/${keyId}`, { data });
};

export const deleteKeyApi = (keyId: string) => {
  return http.request<void>("delete", `/api/manager/litellm/keys/${keyId}`);
};

export const blockKeyApi = (keyId: string) => {
  return http.request<any>("post", `/api/manager/litellm/keys/${keyId}/block`);
};

export const unblockKeyApi = (keyId: string) => {
  return http.request<any>("post", `/api/manager/litellm/keys/${keyId}/unblock`);
};
