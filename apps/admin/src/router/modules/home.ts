import { $t } from "@/plugins/i18n";
import { home } from "@/router/enums";

const { VITE_HIDE_HOME } = import.meta.env;
const Layout = () => import("@/layout/index.vue");

export default {
  path: "/",
  name: "Home",
  component: Layout,
  redirect: "/welcome",
  meta: {
    icon: "ep:home-filled",
    title: $t("menus.pureHome"),
    rank: home,
    roles: ["系统管理员", "平台管理员", "组管理员", "运维人员"]
  },
  children: [
    {
      path: "/welcome",
      name: "Welcome",
      component: () => import("@/views/welcome/index.vue"),
      meta: {
        title: $t("menus.pureHome"),
        showLink: VITE_HIDE_HOME === "true" ? false : true,
        roles: ["系统管理员", "平台管理员", "组管理员", "运维人员"]
      }
    }
  ]
} satisfies RouteConfigsTable;
