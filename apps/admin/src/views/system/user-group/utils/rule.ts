import { reactive } from "vue";
import type { FormRules } from "element-plus";
import { i18n } from "@/plugins/i18n";

const t = i18n.global.t as unknown as (key: string) => string;

export const formRules = reactive(<FormRules>{
  name: [{ required: true, message: t("system.userGroup.rule.nameRequired"), trigger: "blur" }]
});
