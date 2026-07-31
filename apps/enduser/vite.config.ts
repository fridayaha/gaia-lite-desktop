import { defineConfig, loadEnv } from "vite"
import vue from "@vitejs/plugin-vue"
import { resolve } from "path"
import { mockApiPlugin } from "./mockApi"

export default defineConfig(({ mode }) => {
  const env = loadEnv(mode, process.cwd())
  return {
    plugins: [vue(), mockApiPlugin(env)],
    resolve: {
      alias: {
        "@": resolve(__dirname, "src"),
        // @ua/chat 共享对话区组件包——alias 直指源码（不转 pnpm workspace，CI per-app 构建兼容）
        "@ua/chat": resolve(__dirname, "../../packages/ua-chat/src"),
      },
    },
    // noVNC core/util/browser.js 含顶层 await（supportsWebCodecsH264Decode = await ...），
    // 需 es2022+ target（Chrome89+/FF89+/Safari15+ 支持 TLA，现代浏览器均满足）。
    // build.target 管 production；esbuild.target 管 dev 源码转译；optimizeDeps 管 dev 预打包 noVNC。
    esbuild: {
      target: "es2022",
    },
    build: {
      target: "es2022",
    },
    optimizeDeps: {
      esbuildOptions: {
        target: "es2022",
      },
    },
    server: {
      port: 3001,
      proxy: {
        "/api/auth": {
          target: "http://localhost:8002",
          changeOrigin: true,
        },
        "/api/agents": {
          target: "http://localhost:8002",
          changeOrigin: true,
        },
        "/api/gateway": {
          target: "http://localhost:8010",
          changeOrigin: true,
          rewrite: (path: string) => path.replace(/^\/api\/gateway/, ""),
          ws: true, // 浏览器沙箱 VNC WS（/v1/browser/{agent}/vnc）dev 代理
        },
        "/api": {
          target: "http://localhost:8002",
          changeOrigin: true,
        },
      },
    },
  }
})
