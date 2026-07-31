import { $t } from "@/plugins/i18n";
import { agentDefinitions } from "@/router/enums";

const AGENT_DEFINITION_ROLES = ["系统管理员", "平台管理员"];

export default {
  path: "/agent-definitions",
  redirect: "/agent-definitions/index",
  meta: {
    icon: "ri:stack-line",
    title: $t("menus.pureAgentDevelopment"),
    rank: agentDefinitions,
    roles: AGENT_DEFINITION_ROLES
  },
  children: [
    {
      path: "/agent-definitions/index",
      name: "AgentDefinitionList",
      component: () => import("@/views/agent-definitions/index.vue"),
      meta: {
        title: $t("menus.pureAgentDevelopment"),
        roles: AGENT_DEFINITION_ROLES
      }
    },
    {
      path: "/agent-definitions/detail/:id",
      name: "AgentDefinitionDetail",
      component: () => import("@/views/agent-definitions/detail/index.vue"),
      meta: {
        title: $t("definition.detailTitle"),
        showLink: false,
        roles: AGENT_DEFINITION_ROLES
      }
    }
  ]
} satisfies RouteConfigsTable;
