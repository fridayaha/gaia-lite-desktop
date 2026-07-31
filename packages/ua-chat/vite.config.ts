import { defineConfig } from "vite";
import vue from "@vitejs/plugin-vue";
import { resolve } from "path";

// Library-mode build：产出 dist/ua-chat.js + dist/chat.css。
// Phase 0 暂不依赖构建产物——两 app 通过 vite alias 直接消费 src/。
// Phase 2+ 组件就绪后，若需独立产物分发再启用 build。
export default defineConfig({
  plugins: [vue()],
  resolve: {
    alias: {
      "@": resolve(__dirname, "src")
    }
  },
  build: {
    lib: {
      entry: resolve(__dirname, "src/index.ts"),
      name: "UaChat",
      fileName: "ua-chat"
    },
    rollupOptions: {
      external: ["vue"],
      output: {
        globals: { vue: "Vue" },
        assetFileNames: "chat.[ext]"
      }
    }
  }
});
