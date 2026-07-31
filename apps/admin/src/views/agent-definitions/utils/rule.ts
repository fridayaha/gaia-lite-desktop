import type { FormRules } from "element-plus";
import { i18n } from "@/plugins/i18n";

const t = i18n.global.t as unknown as (key: string) => string;

/**
 * 定义层表单校验规则。
 * 仅基本信息（name）与人设+模型（system_prompt/modelGroup）两步，
 * 不含访问范围 / 引擎实例（属实例层）。
 *
 * Dify 引擎不要求 modelGroup（Dify 自管模型），但要求 app_api_key + app_type。
 */
export function makeFormRules(): FormRules {
  return {
    name: [{ required: true, message: t("agent.form.rule.nameRequired"), trigger: "blur" }],
    engine_type: [
      { required: true, message: t("agent.form.rule.engineRequired"), trigger: "change" }
    ],
    // modelGroup 仅 HERMES/OPENCLAW 必填；Dify 引擎跳过（submitStep 不附带 litellm 段）
    modelGroup: [
      {
        validator: (_rule: any, _value: any, callback: any) => {
          // Dify 不需要 modelGroup；其余引擎由后端校验（submitStep 携带 litellm.model_group）
          callback();
        },
        trigger: "change"
      }
    ],
    difyAppType: [
      { required: true, message: t("agent.form.rule.difyAppTypeRequired"), trigger: "change" }
    ],
    difyApiKey: [
      { required: true, message: t("agent.form.rule.difyApiKeyRequired"), trigger: "blur" }
    ]
  };
}
