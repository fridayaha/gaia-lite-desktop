import router from "./index"
import { useAuthStore } from "@/stores/auth"
import { isAccessTokenValid, refreshAccessToken } from "@/api/auth"

router.beforeEach(async (to, _from, next) => {
  const auth = useAuthStore()

  // 主动过期校验：已登录但 access token 过期 → 尝试 refresh，失败跳登录
  // （之前只查 token 是否存在，过期 token 仍能进入页面，撞到 401 才被动处理）
  if (auth.isLoggedIn && !isAccessTokenValid()) {
    const refreshed = await refreshAccessToken()
    if (!refreshed) {
      next({ name: "Login", query: { redirect: to.fullPath } })
      return
    }
  }

  // Try to restore session from localStorage on first load
  if (!auth.isLoggedIn && localStorage.getItem("ua_token")) {
    const restored = await auth.restoreSession()
    if (!restored && to.meta.requiresAuth !== false) {
      next({ name: "Login", query: { redirect: to.fullPath } })
      return
    }
  }

  if (to.meta.requiresAuth && !auth.isLoggedIn) {
    next({ name: "Login", query: { redirect: to.fullPath } })
  } else if (to.name === "Login" && auth.isLoggedIn) {
    next({ name: "AgentList" })
  } else {
    next()
  }
})
