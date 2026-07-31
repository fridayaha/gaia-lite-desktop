import { $t } from "@/plugins/i18n";

const COMMUNITY_WRITE_ROLES = ["系统管理员", "平台管理员", "运维人员"];
const COMMUNITY_AUDIT_ROLES = ["系统管理员", "平台管理员"];

export default {
  path: "/community",
  redirect: "/community/list",
  meta: {
    icon: "ri:group-line",
    title: $t("menus.pureCommunity"),
    showLink: false, // 不在侧边栏显示，通过 navbar 右上角图标进入
  },
  children: [
    {
      path: "/community/list",
      name: "CommunityList",
      component: () => import("@/views/community/index.vue"),
      meta: {
        title: $t("menus.pureCommunity"),
        noAuth: true, // 公开访问，无需登录
      },
    },
    {
      path: "/community/:slug",
      name: "CommunityDetail",
      component: () => import("@/views/community/detail.vue"),
      meta: {
        title: $t("menus.pureCommunity"),
        showLink: false,
        noAuth: true, // 公开访问
        activePath: "/community/list",
      },
    },
    {
      path: "/community/create",
      name: "CommunityCreate",
      component: () => import("@/views/community/editor.vue"),
      meta: {
        title: $t("community.createArticle"),
        showLink: false,
        activePath: "/community/list",
        roles: COMMUNITY_WRITE_ROLES,
      },
    },
    {
      path: "/community/edit/:slug",
      name: "CommunityEdit",
      component: () => import("@/views/community/editor.vue"),
      meta: {
        title: $t("community.editArticle"),
        showLink: false,
        activePath: "/community/list",
        roles: COMMUNITY_WRITE_ROLES,
      },
    },
    {
      path: "/community/audit",
      name: "CommunityAudit",
      component: () => import("@/views/community/audit.vue"),
      meta: {
        title: $t("community.auditTitle"),
        showLink: false,
        activePath: "/community/list",
        roles: COMMUNITY_AUDIT_ROLES,
      },
    },
  ],
} satisfies RouteConfigsTable;
