import { $t } from "@/plugins/i18n";
import { agentInstances } from "@/router/enums";

const AGENT_INSTANCE_ROLES = ["系统管理员", "平台管理员", "运维人员"];

export default {
  path: "/agent-instances",
  redirect: "/agent-instances/index",
  meta: {
    icon: "ri:robot-2-line",
    title: $t("menus.pureAgentInstance"),
    rank: agentInstances,
    roles: AGENT_INSTANCE_ROLES
  },
  children: [
    {
      path: "/agent-instances/index",
      name: "AgentInstanceList",
      component: () => import("@/views/agent-instances/index.vue"),
      meta: {
        title: $t("menus.pureAgentInstance"),
        roles: AGENT_INSTANCE_ROLES
      }
    },
    {
      path: "/agent-instances/detail/:id",
      name: "AgentInstanceDetail",
      component: () => import("@/views/agent-instances/detail/index.vue"),
      meta: {
        title: $t("instance.detailTitle"),
        showLink: false,
        roles: AGENT_INSTANCE_ROLES
      }
    }
  ]
} satisfies RouteConfigsTable;
