import { $t } from "@/plugins/i18n";
import { litellm } from "@/router/enums";

const LITELLM_ROLES = ["系统管理员", "平台管理员"];

export default {
  path: "/litellm",
  redirect: "/litellm/models",
  meta: {
    icon: "ri:router-line",
    title: $t("menus.pureLiteLLM"),
    rank: litellm,
    roles: LITELLM_ROLES
  },
  children: [
    {
      path: "/litellm/models",
      name: "LiteLLMModels",
      component: () => import("@/views/litellm/models/index.vue"),
      meta: { icon: "ri:box-3-line", title: $t("menus.pureLiteLLMModels"), roles: LITELLM_ROLES }
    },
    {
      path: "/litellm/keys",
      name: "LiteLLMKeys",
      component: () => import("@/views/litellm/keys/index.vue"),
      meta: { icon: "ri:key-2-line", title: $t("menus.pureLiteLLMKeys"), roles: LITELLM_ROLES }
    }
  ]
} satisfies RouteConfigsTable;
