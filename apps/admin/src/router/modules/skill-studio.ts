import { $t } from "@/plugins/i18n";
import { skillStudio } from "@/router/enums";

const SKILL_STUDIO_ROLES = ["系统管理员", "平台管理员"];

/**
 * Skill Studio 路由模块 —— 侧边栏一级菜单「技能开发」，紧随「智能体模版」。
 *
 * 工作区列表页（index）为可见菜单项；详情页（detail）隐藏，activePath 指回列表
 * 以保持菜单高亮。原入口在「能力中心」统计卡的「开发技能」按钮，已移除。
 */
export default {
  path: "/skill-studio",
  redirect: "/skill-studio/index",
  meta: {
    icon: "ri:flashlight-line",
    title: $t("menus.pureSkillStudio"),
    rank: skillStudio,
    roles: SKILL_STUDIO_ROLES
  },
  children: [
    {
      path: "/skill-studio/index",
      name: "SkillStudioIndex",
      component: () => import("@/views/skill-studio/index.vue"),
      meta: {
        title: $t("menus.pureSkillStudio"),
        roles: SKILL_STUDIO_ROLES
      }
    },
    {
      path: "/skill-studio/detail/:id",
      name: "SkillStudioDetail",
      component: () => import("@/views/skill-studio/detail.vue"),
      meta: {
        title: $t("menus.pureSkillStudio"),
        showLink: false,
        activePath: "/skill-studio/index",
        roles: SKILL_STUDIO_ROLES
      }
    }
  ]
} satisfies RouteConfigsTable;
