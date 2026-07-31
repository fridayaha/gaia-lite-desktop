import { reactive } from "vue";
import type { FormRules } from "element-plus";
import { i18n } from "@/plugins/i18n";

const t = i18n.global.t as unknown as (key: string) => string;

/** LiteLLM 周期格式：30d / 1mo / 24hr / 60s 等（留空不校验） */
const DURATION_RE = /^\d+\s*(d|mo|hr|h|m|s)$/i;

/** 预算/限速编辑表单规则（字段均可选，仅校验填写时的格式/范围） */
export const editFormRules = reactive<FormRules>({
  max_budget: [{ type: "number", min: 0, message: t("litellm.key.field.invalidNumber"), trigger: "blur" }],
  rpm_limit: [{ type: "number", min: 0, message: t("litellm.key.field.invalidNumber"), trigger: "blur" }],
  tpm_limit: [{ type: "number", min: 0, message: t("litellm.key.field.invalidNumber"), trigger: "blur" }],
  budget_duration: [
    {
      validator: (_rule, value, callback) => {
        if (!value || DURATION_RE.test(value)) callback();
        else callback(new Error(t("litellm.key.field.invalidDuration")));
      },
      trigger: "blur"
    }
  ],
  duration: [
    {
      validator: (_rule, value, callback) => {
        if (!value || DURATION_RE.test(value)) callback();
        else callback(new Error(t("litellm.key.field.invalidDuration")));
      },
      trigger: "blur"
    }
  ]
});
