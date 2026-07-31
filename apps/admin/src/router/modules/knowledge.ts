import { $t } from "@/plugins/i18n";
import { knowledge } from "@/router/enums";

const KNOWLEDGE_ROLES = ["系统管理员", "平台管理员"];

export default {
  path: "/knowledge",
  redirect: "/knowledge/index",
  meta: {
    icon: "ri:book-2-line",
    title: $t("menus.pureKnowledge"),
    rank: knowledge,
    roles: KNOWLEDGE_ROLES
  },
  children: [
    {
      path: "/knowledge/index",
      name: "KnowledgeIndex",
      component: () => import("@/views/knowledge/index.vue"),
      meta: {
        title: $t("menus.pureKnowledge"),
        roles: KNOWLEDGE_ROLES
      }
    }
  ]
} satisfies RouteConfigsTable;
