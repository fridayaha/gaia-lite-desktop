import { $t } from "@/plugins/i18n";
import { system } from "@/router/enums";

const SYSTEM_ROLES = ["系统管理员"];

export default {
  path: "/system",
  redirect: "/system/user/index",
  meta: {
    icon: "ri:settings-3-line",
    title: $t("menus.pureSysManagement"),
    rank: system,
    roles: SYSTEM_ROLES
  },
  children: [
    {
      path: "/system/user/index",
      name: "SystemUser",
      component: () => import("@/views/system/user/index.vue"),
      meta: {
        icon: "ri:admin-line",
        title: $t("menus.pureUser"),
        roles: SYSTEM_ROLES
      }
    },
    {
      path: "/system/role/index",
      name: "SystemRole",
      component: () => import("@/views/system/role/index.vue"),
      meta: {
        icon: "ri:admin-fill",
        title: $t("menus.pureRole"),
        roles: SYSTEM_ROLES
      }
    },
    {
      path: "/system/user-group/index",
      name: "SystemUserGroup",
      component: () => import("@/views/system/user-group/index.vue"),
      meta: {
        icon: "ri:group-line",
        title: $t("menus.pureUserGroup"),
        roles: SYSTEM_ROLES
      }
    },
    {
      path: "/system/engine-config/index",
      name: "SystemEngineConfig",
      component: () => import("@/views/system/engine-config/index.vue"),
      meta: {
        icon: "ri:settings-3-line",
        title: $t("menus.pureEngineConfig"),
        roles: SYSTEM_ROLES
      }
    },
    {
      path: "/system/security-config/index",
      name: "SystemSecurityConfig",
      component: () => import("@/views/system/security-config/index.vue"),
      meta: {
        icon: "ri:shield-keyhole-line",
        title: $t("menus.pureSecurityConfig"),
        roles: SYSTEM_ROLES
      }
    },
    {
      path: "/system/app-release/index",
      name: "SystemAppRelease",
      component: () => import("@/views/system/app-release/index.vue"),
      meta: {
        icon: "ri:app-store-line",
        title: $t("menus.pureAppRelease"),
        roles: SYSTEM_ROLES
      }
    }
  ]
} satisfies RouteConfigsTable;
