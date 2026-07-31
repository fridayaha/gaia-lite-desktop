import { getPluginsList } from "./build/plugins";
import { include, exclude } from "./build/optimize";
import { mockApiPlugin } from "./build/mockApi";
import { type UserConfigExport, type ConfigEnv, loadEnv } from "vite";
import {
  root,
  alias,
  wrapperEnv,
  pathResolve,
  __APP_INFO__
} from "./build/utils";

export default async ({ mode }: ConfigEnv): Promise<UserConfigExport> => {
  const env = loadEnv(mode, root);
  const { VITE_CDN, VITE_PORT, VITE_COMPRESSION, VITE_PUBLIC_PATH } =
    wrapperEnv(env);
  return {
    base: VITE_PUBLIC_PATH,
    root,
    resolve: {
      alias
    },
    // 服务端渲染
    server: {
      // 端口号
      port: VITE_PORT,
      host: "0.0.0.0",
      // 本地跨域代理 https://cn.vitejs.dev/config/server-options.html#server-proxy
      // 契约 §6：controller 已并入 manager，/api/controller 也走 8002
      proxy: {
        "/api/controller": {
          target: "http://localhost:8002",
          changeOrigin: true,
        },
        "/api/manager": {
          target: "http://localhost:8002",
          changeOrigin: true,
        },
        "/api/skill-engine": {
          target: "http://localhost:8002",
          changeOrigin: true,
        },
        "/api/hub": {
          target: "http://localhost:8003",
          changeOrigin: true,
        },
      },
      // 预热文件以提前转换和缓存结果，降低启动期间的初始页面加载时长并防止转换瀑布
      warmup: {
        clientFiles: ["./index.html", "./src/{views,components}/*"]
      }
    },
    plugins: [
      ...(await getPluginsList(VITE_CDN, VITE_COMPRESSION)),
      // 本地 mock 联调（VITE_USE_MOCK=true 开启，dev-only，A/B 就绪后可删）
      mockApiPlugin(env)
    ],
    // https://cn.vitejs.dev/config/dep-optimization-options.html#dep-optimization-options
    optimizeDeps: {
      include,
      exclude
    },
    build: {
      // https://cn.vitejs.dev/guide/build.html#browser-compatibility
      // es2019: 现代浏览器原生支持，避免大量 ES5 转译，显著提升构建速度
      target: "es2019",
      sourcemap: false,
      // 消除打包大小超过500kb警告
      chunkSizeWarningLimit: 4000,
      rolldownOptions: {
        input: {
          index: pathResolve("./index.html", import.meta.url)
        },
        // 静态资源&第三方库分包
        output: {
          chunkFileNames: "static/js/[name]-[hash].js",
          entryFileNames: "static/js/[name]-[hash].js",
          assetFileNames: "static/[ext]/[name]-[hash].[ext]",
          manualChunks(id: string) {
            if (id.includes("vxe-table") || id.includes("plus-pro-components"))
              return "ui-vendor";
            if (id.includes("@pureadmin/utils") || id.includes("@vueuse/core"))
              return "utils-vendor";
            if (id.includes("vue-json-pretty"))
              return "editor-vendor";
          }
        },
        checks: {
          pluginTimings: false,
          toleratedTransform: true
        },
        onLog(level, log) {
          if (log.code === "INVALID_ANNOTATION") return;
        }
      }
    },
    define: {
      __INTLIFY_PROD_DEVTOOLS__: false,
      __APP_INFO__: JSON.stringify(__APP_INFO__)
    }
  };
};
