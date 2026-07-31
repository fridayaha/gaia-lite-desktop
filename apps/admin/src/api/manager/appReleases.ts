import { http } from "@/utils/http";

export type AppReleaseStatus = "draft" | "published";
export type AppReleasePlatform = "android" | "harmony";

export type AppReleaseResponse = {
  id: string;
  platform: AppReleasePlatform;
  version: string;
  display_name: string;
  description: string;
  icon_url: string | null;
  status: AppReleaseStatus;
  manager_url: string | null;
  gateway_url: string | null;
  created_at: string;
  published_at: string | null;
};

export type AppReleaseListResponse = {
  items: AppReleaseResponse[];
  total: number;
  page: number;
  page_size: number;
};

export type AppReleaseLatestResponse = {
  id: string;
  platform: AppReleasePlatform;
  version: string;
  display_name: string;
  description: string;
  icon_url: string | null;
} | null;

export const getAppReleasesApi = (params?: {
  page?: number;
  page_size?: number;
  platform?: AppReleasePlatform;
}) => {
  return http.request<AppReleaseListResponse>("get", "/api/manager/app-releases", {
    params
  });
};

export const uploadBaseApkApi = (data: FormData) => {
  return http.request<AppReleaseResponse>("post", "/api/manager/app-releases", {
    data,
    headers: { "Content-Type": "multipart/form-data" }
  });
};

export const getAppReleaseApi = (id: string) => {
  return http.request<AppReleaseResponse>("get", `/api/manager/app-releases/${id}`);
};

export const updateAppReleaseApi = (
  id: string,
  data: { display_name?: string; description?: string }
) => {
  return http.request<AppReleaseResponse>("patch", `/api/manager/app-releases/${id}`, {
    data
  });
};

export const uploadAppReleaseIconApi = (id: string, data: FormData) => {
  return http.request<AppReleaseResponse>(
    "post",
    `/api/manager/app-releases/${id}/icon`,
    {
      data,
      headers: { "Content-Type": "multipart/form-data" }
    }
  );
};

export const deleteAppReleaseApi = (id: string) => {
  return http.request<any>("delete", `/api/manager/app-releases/${id}`);
};

export const publishAppReleaseApi = (
  id: string,
  data: { manager_url: string; gateway_url: string }
) => {
  return http.request<AppReleaseResponse>(
    "post",
    `/api/manager/app-releases/${id}/publish`,
    {
      data,
      // publish 触发 ApkPatcher：zipalign + apksigner 重签，实测 ~15s，默认 10s 会误报超时
      timeout: 60000
    }
  );
};
