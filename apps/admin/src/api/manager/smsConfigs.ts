import { http } from "@/utils/http";

export type SmsProvider = "aliyun" | "tencent" | "huawei";

export type SmsConfigResponse = {
  id: string;
  provider: SmsProvider;
  is_active: boolean;
  sign_name: string | null;
  template_code: string | null;
  access_key_id_configured: boolean;
  access_key_secret_configured: boolean;
  sdk_app_id: string | null;
  region: string | null;
  daily_limit: number;
  interval_seconds: number;
  created_at: string;
  updated_at: string;
};

export type SmsConfigCreate = {
  provider: SmsProvider;
  sign_name?: string | null;
  template_code?: string | null;
  access_key_id?: string | null; // 留空表示不修改
  access_key_secret?: string | null;
  sdk_app_id?: string | null;
  region?: string | null;
  daily_limit: number;
  interval_seconds: number;
};

export type SmsConfigUpdate = SmsConfigCreate;

export type TestSmsResult = {
  ok: boolean;
  error?: string | null;
};

export const listSmsConfigsApi = () =>
  http.request<SmsConfigResponse[]>("get", "/api/manager/sms-configs");

export const createSmsConfigApi = (data: SmsConfigCreate) =>
  http.request<SmsConfigResponse>("post", "/api/manager/sms-configs", {
    data
  });

export const updateSmsConfigApi = (id: string, data: SmsConfigUpdate) =>
  http.request<SmsConfigResponse>("put", `/api/manager/sms-configs/${id}`, {
    data
  });

export const activateSmsConfigApi = (id: string) =>
  http.request<SmsConfigResponse>(
    "post",
    `/api/manager/sms-configs/${id}/activate`
  );

export const deactivateSmsConfigApi = (id: string) =>
  http.request<SmsConfigResponse>(
    "post",
    `/api/manager/sms-configs/${id}/deactivate`
  );

export const deleteSmsConfigApi = (id: string) =>
  http.request<void>("delete", `/api/manager/sms-configs/${id}`);

export const testSmsConfigApi = (id: string) =>
  http.request<TestSmsResult>(
    "post",
    `/api/manager/sms-configs/${id}/test`
  );
