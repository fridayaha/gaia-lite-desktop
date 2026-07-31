/// <reference types="vitest" />
import { defineConfig } from 'vitest/config';
import react from '@vitejs/plugin-react';
import tailwindcss from '@tailwindcss/vite';
import { resolve } from 'path';

// B6: 发行版开关。EDITION=lite 时前端砍图探索路由（GraphExplorePage 的 cytoscape/
// maplibre import 失去引用 → Vite tree-shake 掉 maplibre ~700KB 大头）。本体建模画布
// OntologyGraph 仍用 cytoscape（纯前端、lite 有用，保留）。full 版零变化。
// __EDITION__ 是构建期常量，App.tsx/Layout.tsx 据此条件注册路由 + 显隐菜单。
const EDITION = process.env.EDITION ?? 'full';

export default defineConfig({
  plugins: [react(), tailwindcss()],
  define: {
    __EDITION__: JSON.stringify(EDITION),
  },
  resolve: {
    alias: { '@': resolve(__dirname, 'src') },
  },
  server: {
    port: 5173,
    proxy: {
      '/ontologies': 'http://localhost:8000',
      '/objects': 'http://localhost:8000',
      // '/actions' 既是 API 前缀(/actions/execute, /actions/definitions)又是
      // 前端页面路由(/actions → ActionsOverview)。页面导航(Accept: text/html)
      // 走 SPA,API 请求(Accept: application/json 等)代理到后端。
      '/actions': {
        target: 'http://localhost:8000',
        bypass(req) {
          // 浏览器页面导航(地址栏输入/链接点击)走 SPA index.html
          if (req.headers.accept?.includes('text/html')) {
            return '/index.html';
          }
          return undefined;
        },
      },
      '/ai': 'http://localhost:8000',
      '/api/auth': 'http://localhost:3000',
      '/api': 'http://localhost:8000',
      '/health': 'http://localhost:8000',
      '/metrics': 'http://localhost:8000',
    },
  },
  test: {
    globals: true,
    environment: 'jsdom',
    setupFiles: ['./src/test/setup.ts'],
    css: false,
  },
});
