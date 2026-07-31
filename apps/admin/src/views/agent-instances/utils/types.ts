import type { AgentInstanceResponse } from "@/api/manager/agentInstances";
import type { AgentDefinitionResponse, AgentVersionResponse } from "@/api/manager/agentDefinitions";
import type { ResourcePoolResponse } from "@/api/manager/resourcePools";

/** Dify 应用对接配置（per-instance，仅 DIFY 引擎实例有值） */
interface DifyConfig {
  base_url: string;
  app_api_key: string;
  app_type: "chat" | "agent" | "workflow" | "";
  app_id: string;
  app_name: string;
  source: "console" | "manual" | "";
}

/** 表单内联数据 */
interface FormItemProps {
  id?: string;
  title: "create" | "edit";
  /** 实例名称 */
  name: string;
  /** 实例描述 */
  description: string;
  /** 选择的定义 ID */
  definition_id: string;
  /** 选择的版本 ID */
  version_id: string;
  /** 选择的资源池 ID */
  resource_pool_id: string;
  /** 归属用户组 ID（隔离单元；单组用户自动填充不显示选择） */
  group_id: string;
  /** 全部定义列表（供选择） */
  allDefinitions: AgentDefinitionResponse[];
  /** 全部版本列表（当前所选定义的版本） */
  allVersions: AgentVersionResponse[];
  /** 全部资源池列表（供选择） */
  allResourcePools: ResourcePoolResponse[];
  /** 当前用户可操作的用户组列表（目标组候选） */
  allGroups: { id: string; name: string }[];
  /** Dify 应用对接配置（编辑模式回填，DIFY 引擎实例专用） */
  dify_config?: DifyConfig;
  /** 运行时开关（编辑模式回填，如浏览器沙箱启用态） */
  runtime_config?: { browser_sandbox?: { enabled?: boolean } };
}

interface FormProps {
  formInline: FormItemProps;
}

export type { FormItemProps, FormProps, AgentInstanceResponse, DifyConfig };
