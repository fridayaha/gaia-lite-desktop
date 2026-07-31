import { $t } from "@/plugins/i18n";
import { hub } from "@/router/enums";

const HUB_ROLES = ["系统管理员", "平台管理员"];

export default {
  path: "/hub",
  redirect: "/hub/index",
  meta: {
    icon: "ri:store-2-line",
    title: $t("menus.pureHub"),
    rank: hub,
    roles: HUB_ROLES
  },
  children: [
    {
      path: "/hub/index",
      name: "HubIndex",
      component: () => import("@/views/hub/index.vue"),
      meta: {
        title: $t("menus.pureHub"),
        roles: HUB_ROLES
      }
    },
    {
      path: "/hub/detail/:id",
      name: "HubDetail",
      component: () => import("@/views/hub/detail.vue"),
      meta: {
        title: $t("menus.pureHub"),
        showLink: false,
        activePath: "/hub/index",
        roles: HUB_ROLES
      }
    }
  ]
} satisfies RouteConfigsTable;
