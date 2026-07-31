import { createApp } from "vue"
import { createPinia } from "pinia"
import App from "./App.vue"
import router from "./router"
import "./router/guard"  // 注册路由守卫
import { ensureAuthenticated } from "./api/auth"
// @ua/chat 共享对话区样式（聊天类 + CSS 变量主题，抽自原 style.css）
import "@ua/chat/styles/chat.css"
import "./style.css"

const app = createApp(App)
app.use(createPinia())
app.use(router)
app.mount("#app")

// 切走标签页后 token 可能过期，重聚焦时主动校验：过期则尝试 refresh，失败跳登录。
// 之前只在发请求撞 401 时被动处理，用户回来看到的是「已登录但实际失效」的假状态。
document.addEventListener("visibilitychange", () => {
  if (document.visibilityState === "visible" && localStorage.getItem("ua_token")) {
    void ensureAuthenticated()
  }
})
