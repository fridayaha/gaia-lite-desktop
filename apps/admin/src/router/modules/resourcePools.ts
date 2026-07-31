import { $t } from "@/plugins/i18n";
import { resourcePools } from "@/router/enums";

const RESOURCE_POOL_ROLES = ["系统管理员", "平台管理员"];

export default {
  path: "/resource-pools",
  redirect: "/resource-pools/index",
  meta: {
    icon: "ri:server-line",
    title: $t("menus.pureResourcePools"),
    rank: resourcePools,
    roles: RESOURCE_POOL_ROLES
  },
  children: [
    {
      path: "/resource-pools/index",
      name: "ResourcePoolList",
      component: () => import("@/views/resource-pools/index.vue"),
      meta: { title: $t("menus.pureResourcePools"), roles: RESOURCE_POOL_ROLES }
    },
    {
      path: "/resource-pools/detail/:id",
      name: "ResourcePoolDetail",
      component: () => import("@/views/resource-pools/detail/index.vue"),
      meta: { title: $t("resourcePool.detailTitle"), showLink: false, roles: RESOURCE_POOL_ROLES }
    }
  ]
};
