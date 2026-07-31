import { http } from "@/utils/http";

export type DifyEngineMode = "MANAGED" | "EXTERNAL";

export type EngineConfigResponse = {
  id: string;
  engine_type: "DIFY" | "HERMES" | "OPENCLAW";
  mode: DifyEngineMode;
  base_url: string | null;
  admin_email: string | null;
  admin_password_configured: boolean;
  langfuse_host: string | null;
  langfuse_public_key: string | null;
  langfuse_secret_key_configured: boolean;
  created_at: string;
  updated_at: string;
};

export type EngineConfigUpsert = {
  engine_type?: "DIFY";
  mode: DifyEngineMode;
  base_url?: string | null;
  admin_email?: string | null;
  admin_password?: string | null; // 留空表示不修改
  langfuse_host?: string | null; // 留空表示不修改
  langfuse_public_key?: string | null; // 留空表示不修改
  langfuse_secret_key?: string | null; // 留空表示不修改
};

export type DifyAppOption = {
  id: string;
  name: string;
  mode: string; // chat / agent-chat / advanced-chat / workflow
  description: string | null;
};

export type DifyAppSelectResult = {
  base_url: string;
  app_id: string;
  app_name: string;
  app_type: "chat" | "agent" | "workflow";
  app_api_key: string;
};

export type TestConnectionResult = {
  ok: boolean;
  apps_count?: number | null;
  error?: string | null;
};

export type TestLangfuseResult = {
  ok: boolean;
  trace_count?: number | null;
  error?: string | null;
};

export type VerifyServiceApiResult = {
  name: string;
  mode: string;
  app_type: "chat" | "agent" | "workflow" | null;
  description: string;
};

export const getEngineConfigApi = (engineType: "DIFY" = "DIFY") =>
  http.request<EngineConfigResponse | null>("get", "/api/manager/engine-configs", {
    params: { engine_type: engineType }
  });

export const upsertEngineConfigApi = (data: EngineConfigUpsert) =>
  http.request<EngineConfigResponse>("post", "/api/manager/engine-configs", {
    data
  });

export const deleteEngineConfigApi = (configId: string) =>
  http.request<void>("delete", `/api/manager/engine-configs/${configId}`);

export const testConnectionApi = (configId: string) =>
  http.request<TestConnectionResult>(
    "post",
    `/api/manager/engine-configs/${configId}/test-connection`
  );

export const testLangfuseApi = (configId: string) =>
  http.request<TestLangfuseResult>(
    "post",
    `/api/manager/engine-configs/${configId}/test-langfuse`
  );

export const listDifyAppsApi = (configId: string) =>
  http.request<DifyAppOption[]>(
    "get",
    `/api/manager/engine-configs/${configId}/dify-apps`
  );

export const selectDifyAppApi = (configId: string, appId: string) =>
  http.request<DifyAppSelectResult>(
    "post",
    `/api/manager/engine-configs/${configId}/dify-apps/${appId}/select`
  );

export const verifyDifyServiceApi = (baseUrl: string, apiKey: string) =>
  http.request<VerifyServiceApiResult>(
    "post",
    "/api/manager/agent-instances/verify-dify-service-api",
    { data: { base_url: baseUrl, app_api_key: apiKey } }
  );
