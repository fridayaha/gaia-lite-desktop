import { createRouter, createWebHashHistory } from "vue-router"
import type { RouteRecordRaw } from "vue-router"

const routes: RouteRecordRaw[] = [
  {
    path: "/login",
    name: "Login",
    component: () => import("@/views/LoginPage.vue"),
    meta: { requiresAuth: false },
  },
  {
    path: "/agents",
    name: "AgentList",
    component: () => import("@/views/AgentListPage.vue"),
    meta: { requiresAuth: true },
  },
  {
    path: "/agents/:id",
    name: "AgentChat",
    component: () => import("@/views/AgentChatPage.vue"),
    meta: { requiresAuth: true },
  },
  {
    path: "/",
    redirect: "/agents",
  },
  {
    path: "/:pathMatch(.*)*",
    redirect: "/agents",
  },
]

const router = createRouter({
  history: createWebHashHistory(),
  routes,
})

export default router
