import type { EngineType } from "@/api/manager/agentDefinitions";

interface FormItemProps {
  id?: string;
  title: "create" | "edit";
  name: string;
  description: string;
  avatar_color: string;
  /** 引擎类型（HERMES / OPENCLAW / DIFY） */
  engine_type: EngineType;
  /** 归属用户组 ID（隔离单元；单组用户自动填充） */
  group_id: string;
  /** 人设配置（含 system_prompt） */
  persona_config: Record<string, any>;
  /** 模型配置（含 system_prompt / litellm，dify 配置已下沉到实例层 AgentInstance.dify_config） */
  model_settings: Record<string, any>;
  /** 技能配置（JSON 对象） */
  skill_config: Record<string, any>;
  /** 记忆配置（JSON 对象） */
  memory_config: Record<string, any>;
  /** LiteLLM 模型组（从 model_settings.litellm.model_group 拆解给 UI） */
  modelGroup: string;
  /** 系统提示词（SOUL.md） */
  system_prompt: string;
  /** 当前用户可操作的用户组列表（目标组候选） */
  allGroups: { id: string; name: string }[];
}

interface FormProps {
  formInline: FormItemProps;
}

export type { FormItemProps, FormProps };
