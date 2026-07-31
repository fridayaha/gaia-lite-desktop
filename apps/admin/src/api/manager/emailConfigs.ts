import { http } from "@/utils/http";

export type EmailProvider = "smtp" | "aliyun" | "tencent" | "huawei";

export type EmailEncryption = "none" | "ssl" | "starttls";

export type EmailConfigResponse = {
  id: string;
  provider: EmailProvider;
  is_active: boolean;
  smtp_host: string | null;
  smtp_port: number | null;
  encryption: EmailEncryption | null;
  username: string | null;
  password_configured: boolean;
  access_key_id_configured: boolean;
  access_key_secret_configured: boolean;
  region: string | null;
  from_email: string | null;
  from_name: string | null;
  daily_limit: number;
  interval_seconds: number;
  created_at: string;
  updated_at: string;
};

export type EmailConfigCreate = {
  provider: EmailProvider;
  smtp_host?: string | null;
  smtp_port?: number | null;
  encryption?: EmailEncryption | null;
  username?: string | null;
  password?: string | null; // smtp 必填（首次创建）；留空表示不修改（update 时）
  access_key_id?: string | null; // cloud provider 必填（首次创建）；留空表示不修改
  access_key_secret?: string | null;
  region?: string | null;
  from_email?: string | null;
  from_name?: string | null;
  daily_limit: number;
  interval_seconds: number;
};

// EmailConfigUpdate 同 Create，所有可选字段留空表示不修改
export type EmailConfigUpdate = EmailConfigCreate;

export type TestEmailResult = {
  ok: boolean;
  error?: string | null;
};

export const listEmailConfigsApi = () =>
  http.request<EmailConfigResponse[]>("get", "/api/manager/email-configs");

export const createEmailConfigApi = (data: EmailConfigCreate) =>
  http.request<EmailConfigResponse>("post", "/api/manager/email-configs", {
    data
  });

export const updateEmailConfigApi = (
  configId: string,
  data: EmailConfigUpdate
) =>
  http.request<EmailConfigResponse>(
    "put",
    `/api/manager/email-configs/${configId}`,
    { data }
  );

export const activateEmailConfigApi = (configId: string) =>
  http.request<EmailConfigResponse>(
    "post",
    `/api/manager/email-configs/${configId}/activate`
  );

export const deactivateEmailConfigApi = (configId: string) =>
  http.request<EmailConfigResponse>(
    "post",
    `/api/manager/email-configs/${configId}/deactivate`
  );

export const deleteEmailConfigApi = (configId: string) =>
  http.request<void>("delete", `/api/manager/email-configs/${configId}`);

export const testEmailConfigApi = (configId: string) =>
  http.request<TestEmailResult>(
    "post",
    `/api/manager/email-configs/${configId}/test`
  );
