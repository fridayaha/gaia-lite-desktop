import type { FormRules } from "element-plus";
import { i18n } from "@/plugins/i18n";

const t = i18n.global.t as unknown as (key: string) => string;

/**
 * 构建表单校验规则。
 * group_id 的必选校验仅当用户属多个组时生效（单组用户自动填充），故以 getter 注入。
 * resource_pool_id 在 Dify 外接模式下不显示也不校验（外接实例直接调外部 Dify API，无 k8s 资源）。
 */
export function makeFormRules(
  getGroupCount: () => number,
  getIsDifyExternal: () => boolean
): FormRules {
  return {
    name: [{ required: true, message: t("instance.form.rule.nameRequired"), trigger: "blur" }],
    definition_id: [
      { required: true, message: t("instance.form.rule.definitionRequired"), trigger: "change" }
    ],
    version_id: [
      { required: true, message: t("instance.form.rule.versionRequired"), trigger: "change" }
    ],
    resource_pool_id: [
      {
        validator: (_rule, value, callback) => {
          if (!getIsDifyExternal() && !value) {
            callback(new Error(t("instance.form.rule.resourcePoolRequired")));
          } else {
            callback();
          }
        },
        trigger: "change"
      }
    ],
    group_id: [
      {
        validator: (_rule, value, callback) => {
          if (getGroupCount() > 1 && !value) {
            callback(new Error(t("instance.form.rule.groupRequired")));
          } else {
            callback();
          }
        },
        trigger: "change"
      }
    ]
  };
}
