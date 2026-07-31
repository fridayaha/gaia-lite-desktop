import { http } from "@/utils/http";

export type UserResponse = {
  id: string;
  username: string;
  real_name: string | null;
  email: string | null;
  phone: string | null;
  email_verified: boolean;
  phone_verified: boolean;
  is_active: boolean;
  roles: string[];
  created_at: string;
  failed_login_count: number;
  locked_until: string | null;
  is_locked: boolean;
  locked_remaining_seconds: number | null;
};

export type UserListResponse = {
  items: UserResponse[];
  total: number;
  page: number;
  page_size: number;
};

export const getUsersApi = (params?: {
  page?: number;
  page_size?: number;
  search?: string;
  is_active?: boolean;
  role_id?: string;
}) => {
  return http.request<UserListResponse>("get", "/api/manager/users", { params });
};

export const createUserApi = (data: {
  username: string;
  real_name?: string;
  email: string;
  phone?: string;
  password: string;
  role_ids?: string[];
}) => {
  return http.request<UserResponse>("post", "/api/manager/users", { data });
};

export const getUserApi = (id: string) => {
  return http.request<UserResponse>("get", `/api/manager/users/${id}`);
};

export const updateUserApi = (
  id: string,
  data: {
    username?: string;
    real_name?: string;
    email?: string;
    phone?: string;
    password?: string;
    is_active?: boolean;
    role_ids?: string[];
  }
) => {
  return http.request<UserResponse>("put", `/api/manager/users/${id}`, { data });
};

export const deleteUserApi = (id: string) => {
  return http.request<any>("delete", `/api/manager/users/${id}`);
};

export const unlockUserApi = (id: string) => {
  return http.request<UserResponse>("post", `/api/manager/users/${id}/unlock`);
};

// ── 0.8.110 邮箱/手机认证（admin 发起）──

export const initiateEmailVerifyApi = (id: string) => {
  return http.request<{ sent: boolean; expires_in: number }>(
    "post",
    `/api/manager/users/${id}/initiate-email-verify`
  );
};

export const initiatePhoneVerifyApi = (id: string) => {
  return http.request<{ sent: boolean; expires_in: number }>(
    "post",
    `/api/manager/users/${id}/initiate-phone-verify`
  );
};

export const verifyUserEmailApi = (id: string, code: string) => {
  return http.request<UserResponse>(
    "post",
    `/api/manager/users/${id}/verify-email`,
    { data: { code } }
  );
};

export const verifyUserPhoneApi = (id: string, code: string) => {
  return http.request<UserResponse>(
    "post",
    `/api/manager/users/${id}/verify-phone`,
    { data: { code } }
  );
};

// ── 用户独立会话空间占用（删用户确认提示用）──

export type UserProfileOccupancyItem = {
  instance_id: string;
  instance_name: string | null;
  profile_name: string;
  created_at: string | null;
};

export type UserProfileOccupancyResponse = {
  count: number;
  items: UserProfileOccupancyItem[];
};

export const getUserProfilesApi = (id: string) => {
  return http.request<UserProfileOccupancyResponse>(
    "get",
    `/api/manager/users/${id}/profiles`
  );
};

// ── IM Bindings ──

export type ImBindingResponse = {
  id: string;
  user_id: string;
  channel_type: string;
  im_user_id: string;
  im_user_name: string | null;
  created_at: string;
};

export type ImBindingListResponse = {
  items: ImBindingResponse[];
  total: number;
};

export const getUserImBindingsApi = (userId: string) => {
  return http.request<ImBindingListResponse>(
    "get",
    `/api/manager/users/${userId}/im-bindings`
  );
};

export const createUserImBindingApi = (
  userId: string,
  data: { channel_type: string; im_user_id: string; im_user_name?: string }
) => {
  return http.request<ImBindingResponse>(
    "post",
    `/api/manager/users/${userId}/im-bindings`,
    { data }
  );
};

export const deleteUserImBindingApi = (userId: string, bindingId: string) => {
  return http.request<any>(
    "delete",
    `/api/manager/users/${userId}/im-bindings/${bindingId}`
  );
};

// ── Business Binding（1:1，业务系统用户身份）──

export type BusinessBindingResponse = {
  id: string;
  user_id: string;
  business_username: string;
  business_phone: string | null;
  business_email: string | null;
  created_at: string;
};

export const getUserBusinessBindingApi = (userId: string) => {
  return http.request<BusinessBindingResponse | null>(
    "get",
    `/api/manager/users/${userId}/business-bindings`
  );
};

export const upsertUserBusinessBindingApi = (
  userId: string,
  data: { business_username: string; business_phone?: string; business_email?: string }
) => {
  return http.request<BusinessBindingResponse>(
    "put",
    `/api/manager/users/${userId}/business-bindings`,
    { data }
  );
};

export const deleteUserBusinessBindingApi = (userId: string) => {
  return http.request<any>(
    "delete",
    `/api/manager/users/${userId}/business-bindings`
  );
};
