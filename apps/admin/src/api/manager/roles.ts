import { http } from "@/utils/http";

export type RoleResponse = {
  id: string;
  name: string;
  description: string;
  permission_codes: string[];
  user_count: number;
  created_at: string;
};

export type PermissionResponse = {
  id: string;
  name: string;
  code: string;
  description: string;
  resource_type: string;
};

export const getRolesApi = () => {
  return http.request<RoleResponse[]>("get", "/api/manager/roles");
};

export const createRoleApi = (data: {
  name: string;
  description?: string;
  permission_ids?: string[];
}) => {
  return http.request<RoleResponse>("post", "/api/manager/roles", { data });
};

export const getRoleApi = (id: string) => {
  return http.request<RoleResponse>("get", `/api/manager/roles/${id}`);
};

export const updateRoleApi = (
  id: string,
  data: {
    name?: string;
    description?: string;
    permission_ids?: string[];
  }
) => {
  return http.request<RoleResponse>("put", `/api/manager/roles/${id}`, { data });
};

export const deleteRoleApi = (id: string) => {
  return http.request<any>("delete", `/api/manager/roles/${id}`);
};

export const getAllPermissionsApi = () => {
  return http.request<PermissionResponse[]>("get", "/api/manager/roles/permissions/all");
};
