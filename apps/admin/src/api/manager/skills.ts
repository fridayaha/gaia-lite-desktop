import { http } from "@/utils/http";

/** 技能配置参数定义 */
export interface SkillConfigParam {
  name: string;
  label: string;
  type: "string" | "number" | "boolean" | "select";
  options?: string[];
  default?: any;
  description?: string;
  /** 标记为凭证参数：前端用 password 输入且不回显明文，后端加密存储 */
  secret?: boolean;
}

/** Skill 绑定（definition 维度） */
export interface AgentSkill {
  id: string;
  name: string;
  description: string;
  icon: string;
  enabled: boolean;
  version: string;
  author: string;
  config?: Record<string, any>;
  configParams?: SkillConfigParam[];
  usageCount: number;
  engine?: string[];
  builtin?: boolean;
  installed?: boolean;
  /** skill 来源：preset（出厂预置）/ local（本地上传）/ ua_hub（UA 技能市场）/ builtin（引擎内置） */
  source?: string;
}

export type SkillListResponse = {
  items: AgentSkill[];
  engineDeployed?: boolean;
};

const base = (definitionId: string) => `/api/manager/agent-definitions/${definitionId}/skills`;

export const getSkillsApi = (definitionId: string) => {
  return http.request<SkillListResponse>("get", base(definitionId));
};

export const toggleSkillApi = (definitionId: string, skillId: string, enabled: boolean) => {
  return http.request<AgentSkill>("put", `${base(definitionId)}/${skillId}`, {
    data: { enabled }
  });
};

/** 本地上传安装技能（zip 包） */
export const installSkillApi = (definitionId: string, file: File) => {
  const formData = new FormData();
  formData.append("file", file);
  return http.request<AgentSkill>("post", `${base(definitionId)}/install`, {
    data: formData,
    headers: { "Content-Type": "multipart/form-data" }
  });
};

/** 解析技能 zip 包（仅预览，不安装） */
export const previewSkillApi = (definitionId: string, file: File) => {
  const formData = new FormData();
  formData.append("file", file);
  return http.request<{ manifest: AgentSkill; warnings: string[]; safe: boolean }>(
    "post",
    `${base(definitionId)}/preview`,
    { data: formData, headers: { "Content-Type": "multipart/form-data" } }
  );
};

export const uninstallSkillApi = (definitionId: string, skillId: string) => {
  return http.request<void>("delete", `${base(definitionId)}/${skillId}`);
};

export const getMarketplaceSkillsApi = () => {
  return http.request<SkillListResponse>("get", "/api/manager/skills/marketplace");
};

export const installMarketplaceSkillApi = (definitionId: string, skillId: string) => {
  return http.request<AgentSkill>("post", `${base(definitionId)}/marketplace/${skillId}/install`);
};

export const updateSkillOrderApi = (definitionId: string, skillIds: string[]) => {
  return http.request<void>("put", `${base(definitionId)}/order`, {
    data: { skill_ids: skillIds }
  });
};

/** 获取 skill 凭证配置状态（不回显明文，仅返回已配置的 secret 参数名） */
export const getSkillCredentialStatusApi = (definitionId: string, skillId: string) => {
  return http.request<{ configured: string[]; target_base_url?: string }>(
    "get",
    `${base(definitionId)}/${skillId}/credentials`
  );
};

/** 保存 skill 凭证（secret 参数加密存储，仅传非空值，空值表示不修改） */
export const saveSkillCredentialsApi = (
  definitionId: string,
  skillId: string,
  credentials: Record<string, string>
) => {
  return http.request<void>("put", `${base(definitionId)}/${skillId}/credentials`, {
    data: { credentials }
  });
};

/** 获取 skill 非 secret 配置（回填用，含未填参数的 default 兜底） */
export const getSkillConfigApi = (definitionId: string, skillId: string) => {
  return http.request<{ values: Record<string, any> }>(
    "get",
    `${base(definitionId)}/${skillId}/config`
  );
};

/** 保存 skill 非 secret 配置（落库 + 重渲染 SKILL.md 变量） */
export const saveSkillConfigApi = (
  definitionId: string,
  skillId: string,
  config: Record<string, any>
) => {
  return http.request<{ ok: boolean; fanout: { total: number; ok: number; failed: number } }>(
    "put",
    `${base(definitionId)}/${skillId}/config`,
    { data: { config } }
  );
};
