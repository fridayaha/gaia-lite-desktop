import { reactive } from "vue";
import type { FormRules } from "element-plus";
import { isEmail } from "@pureadmin/utils";
import { zxcvbn, getPasswordStrengthHint } from "@/utils/zxcvbn";
import { i18n } from "@/plugins/i18n";

const t = i18n.global.t as unknown as (key: string) => string;

/** 自定义表单规则校验 */
export const formRules = reactive(<FormRules>{
  username: [{ required: true, message: t("system.user.form.rule.usernameRequired"), trigger: "blur" }],
  password: [
    { required: true, message: t("system.user.form.rule.passwordRequired"), trigger: "blur" },
    { min: 8, message: t("system.user.form.rule.passwordWeak"), trigger: "blur" },
    {
      validator: (rule, value, callback) => {
        if (!value) {
          callback();
          return;
        }
        // 与后端 _validate_password_strength 对齐：score < 3 拒绝，文案用 zxcvbn 给的具体 warning
        const score = zxcvbn(value).score;
        if (score < 3) {
          const hint = getPasswordStrengthHint(value) || t("system.user.form.rule.passwordStrength");
          callback(new Error(hint));
        } else {
          callback();
        }
      },
      trigger: "blur"
    }
  ],
  email: [
    {
      validator: (rule, value, callback) => {
        // 0.8.110 两态：email 可选；填了才校验格式
        if (value && !isEmail(value)) {
          callback(new Error(t("system.user.form.rule.emailInvalid")));
        } else {
          callback();
        }
      },
      trigger: "blur"
    }
  ]
});
