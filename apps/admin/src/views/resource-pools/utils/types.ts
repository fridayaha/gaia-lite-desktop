import type { ResourcePoolResponse } from "@/api/manager/resourcePools";

export type FormItemProps = {
  /** Dialog title: "新增" or "修改" */
  title: string;
  /** Pool id (only for edit) */
  id?: string;
  name: string;
  description: string;
  /** 归属用户组：""=平台共享池（仅平台管理员），组 id=组私有池 */
  group_id: string;
  /** 当前用户可操作的用户组列表（目标组候选） */
  allGroups: { id: string; name: string }[];
  /** 是否平台管理员（决定能否选"平台共享"） */
  isPlatformAdmin: boolean;
  min_cpu: string;
  max_cpu: string;
  min_memory: string;
  max_memory: string;
  min_replicas: number;
  max_replicas: number;
  auto_recycle: boolean;
  idle_suspend_minutes: number;
  idle_destroy_hours: number;
  max_sessions_per_pod: number;
};

export type FormProps = {
  formInline: FormItemProps;
};

export type { ResourcePoolResponse };
