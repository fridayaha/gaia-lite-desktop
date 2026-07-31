import { http } from "@/utils/http";

/** Our backend login response */
export type BackendLoginResult = {
  access_token: string;
  refresh_token: string;
  token_type: string;
};

/** Compat type for template's RefreshTokenResult */
export type RefreshTokenResult = {
  accessToken?: string;
  data?: { accessToken: string; refreshToken: string; expires: Date };
};

/** Compat type for template's UserResult */
export type UserResult = {
  accessToken?: string;
  data?: { accessToken: string; refreshToken: string; expires: Date };
};

/** Compat type for UserInfo used by account-settings */
export type UserInfo = {
  avatar: string;
  username: string;
  nickname: string;
  email: string;
  phone: string;
  avatar_url: string;
};

export type UserInfoResult = {
  code: number;
  message: string;
  data: UserInfo;
};

type ResultTable = {
  code: number;
  message: string;
  data?: {
    list: Array<any>;
    total?: number;
    pageSize?: number;
    currentPage?: number;
  };
};

/** Login to our backend API */
export const getLogin = (data?: object) => {
  return http.request<BackendLoginResult>("post", "/api/manager/auth/login", { data });
};

/** Refresh token */
export const refreshTokenApi = (data?: object) => {
  return http.request<BackendLoginResult>("post", "/api/manager/auth/refresh", { data });
};

/** Get current user info */
export const getMine = (data?: object) => {
  return http.request<any>("get", "/api/manager/auth/me", { data });
};

/** Account settings - logs (uses mock) */
export const getMineLogs = (data?: object) => {
  return http.request<ResultTable>("get", "/api/manager/mine-logs", { data });
};

/** Update self profile (real_name / email / phone / avatar_url) */
export const updateMe = (data?: object) => {
  return http.request<UserInfoResult>("patch", "/api/manager/auth/me", { data });
};

/** Change password (old + new) */
export const changePassword = (data?: object) => {
  return http.request<{ code: number; message: string }>(
    "post",
    "/api/manager/auth/change-password",
    { data }
  );
};

/** Logout — record auth.logout audit log on backend (best-effort, token 不失效) */
export const logoutApi = () => {
  return http.request<{ code: number; message: string }>(
    "post",
    "/api/manager/auth/logout"
  );
};

/** Upload avatar to MinIO public bucket, returns new avatar_url */
export const uploadAvatar = (formData: FormData) => {
  return http.request<{ code: number; message: string; data: { avatar_url: string } }>(
    "post",
    "/api/manager/auth/avatar",
    {
      data: formData,
      headers: { "Content-Type": "multipart/form-data" }
    }
  );
};

/** Fetch preset avatar relative paths (12 items). */
export const getPresetAvatars = () => {
  return http.request<{ code: number; message: string; data: { items: string[] } }>(
    "get",
    "/api/manager/auth/preset-avatars"
  );
};

/** Query active verification channels — public endpoint, no Bearer token */
export const getVerificationChannels = () => {
  return http.request<{ email: boolean; sms: boolean }>(
    "get",
    "/api/manager/auth/verification-channels"
  );
};

/** Login by verified email/phone + password */
export const loginByContact = (data: {
  contact_type: "email" | "phone";
  contact: string;
  password: string;
  captcha_id?: string;
  captcha_answer?: string;
}) => {
  return http.request<BackendLoginResult>(
    "post",
    "/api/manager/auth/login-by-contact",
    { data }
  );
};

/** Login by verified phone + SMS code (no password) */
export const loginBySmsCode = (data: { phone: string; code: string }) => {
  return http.request<BackendLoginResult>(
    "post",
    "/api/manager/auth/login-by-sms-code",
    { data }
  );
};
