import { http } from "@/utils/http";

export type UserGroupResponse = {
  id: string;
  name: string;
  code: string;
  description: string;
  member_count: number;
  created_at: string;
};

export type UserGroupDetailResponse = UserGroupResponse & {
  members: Array<{
    id: string;
    username: string;
    email: string;
  }>;
};

export const getUserGroupsApi = () => {
  return http.request<UserGroupResponse[]>("get", "/api/manager/user-groups");
};

export const createUserGroupApi = (data: {
  name: string;
  description?: string;
  member_ids?: string[];
}) => {
  return http.request<UserGroupResponse>("post", "/api/manager/user-groups", { data });
};

export const getUserGroupApi = (id: string) => {
  return http.request<UserGroupDetailResponse>("get", `/api/manager/user-groups/${id}`);
};

export const updateUserGroupApi = (
  id: string,
  data: {
    name?: string;
    description?: string;
    member_ids?: string[];
  }
) => {
  return http.request<UserGroupResponse>("put", `/api/manager/user-groups/${id}`, { data });
};

export const deleteUserGroupApi = (id: string) => {
  return http.request<any>("delete", `/api/manager/user-groups/${id}`);
};
