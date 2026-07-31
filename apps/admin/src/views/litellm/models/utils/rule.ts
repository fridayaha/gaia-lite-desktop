import type { FormRules } from "element-plus";
import { i18n } from "@/plugins/i18n";

const t = i18n.global.t as unknown as (key: string) => string;

/** 模型组新建/编辑表单规则工厂：api_key 仅 create 模式必填（编辑留空=不变） */
export function makeFormRules(mode: "create" | "edit"): FormRules {
  return {
    model_name: [{ required: true, message: t("litellm.model.msg.required"), trigger: "blur" }],
    model: [{ required: true, message: t("litellm.model.msg.required"), trigger: "blur" }],
    api_key: [
      {
        required: mode === "create",
        message: t("litellm.model.msg.required"),
        trigger: "blur"
      }
    ]
  };
}

/** 价格编辑表单规则：留空可（=未配置），填则非负 */
export const priceFormRules: FormRules = {
  input_cost_per_1m_tokens: [
    { type: "number", min: 0, message: t("litellm.key.field.invalidNumber"), trigger: "blur" }
  ],
  output_cost_per_1m_tokens: [
    { type: "number", min: 0, message: t("litellm.key.field.invalidNumber"), trigger: "blur" }
  ]
};
